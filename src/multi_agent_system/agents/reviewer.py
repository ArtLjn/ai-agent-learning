"""工单审核 Agent：检查处理结果质量，返回 0-1 评分。"""

import json

from loguru import logger
from openai import APIConnectionError, APIError, AuthenticationError, RateLimitError

from src.multi_agent_system.config import Settings
from src.multi_agent_system.core import CachedLLMClient, fallback_registry, track_agent_execution, with_retry
from src.multi_agent_system.core.exceptions import NonRetryableError, RetryableError
from src.multi_agent_system.core.json_parser import parse_json_response

__all__ = ["ReviewerAgent"]

# 审核提示词（D-02：从 prompts/review.j2 加载；含 jinja2 占位符）
from jinja2 import Template

from src.multi_agent_system.prompts import get_prompt_template

_REVIEWER_SYSTEM_PROMPT = get_prompt_template("review")

_NON_RETRYABLE_ISSUE_TYPES = {"knowledge_gap", "needs_clarification", "out_of_scope"}


class ReviewerAgent:
    """工单审核 Agent：检查处理结果质量，返回 0-1 评分。

    通过 LLM 从准确性、可行性、完整性和专业性四个维度
    评估工单处理结果的质量。

    Args:
        model: 模型名称
        api_key: API 密钥
        base_url: API 基础地址
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        task_type: str = "review",
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._task_type = task_type
        self._client: CachedLLMClient | None = None
        # D-02：DB 覆盖的 prompt 模板（None 表示用代码默认 _REVIEWER_SYSTEM_PROMPT）
        self._prompt_override: str | None = None

    def set_prompt_override(self, template: str | None) -> None:
        """注入 DB 中的 active prompt 模板；传 None 还原代码默认。

        注意：reviewer 的 system prompt 含 {ticket_info}/{review_criteria}/{history}
        占位符，覆盖时需保留这些占位符以避免 .format() 失败。
        """
        self._prompt_override = template

    @property
    def client(self) -> CachedLLMClient:
        """延迟初始化带缓存的 LLM 客户端。"""
        if self._client is None:
            settings = Settings()
            self._client = CachedLLMClient(
                api_key=self._api_key or settings.llm_api_key,
                base_url=self._base_url or settings.llm_base_url,
                model=self._model,
            )
        return self._client

    @track_agent_execution("reviewer")
    async def review(
        self,
        content: str,
        processing_result: str,
        category: str,
    ) -> dict:
        """审核处理结果，返回评分和反馈。

        重试和降级由 @with_retry 装饰器统一处理。

        Args:
            content: 原始工单内容
            processing_result: 处理结果文本
            category: 工单分类

        Returns:
            包含 score（0-1）和 feedback 的字典
        """
        return await self._review_by_llm(content, processing_result, category)

    @with_retry(
        max_retries=3,
        backoff_base=2.0,
        retryable_exceptions=(APIError, APIConnectionError, RateLimitError, RetryableError),
        fallback=lambda self, content, processing_result, category: fallback_registry.execute(
            "reviewer.review"
        ),
    )
    async def _review_by_llm(
        self,
        content: str,
        processing_result: str,
        category: str,
    ) -> dict:
        """通过 LLM 进行质量审核。

        由 @with_retry 装饰器统一处理重试和降级逻辑。

        Args:
            content: 原始工单内容
            processing_result: 处理结果文本
            category: 工单分类

        Returns:
            审核结果字典

        Raises:
            RetryableError: OpenAI API 可重试错误
            NonRetryableError: 认证失败或 JSON 解析失败
        """
        system_prompt = Template(
            self._prompt_override if self._prompt_override else _REVIEWER_SYSTEM_PROMPT
        ).render(
            content=content,
            category=category,
            processing_result=processing_result,
        )

        logger.info(f"[Reviewer] 调用 LLM 模型: {self._model}")
        logger.debug(f"[Reviewer] 审核内容:\n工单: {content}\n结果: {processing_result}")

        try:
            response = await self.client.chat_completions_create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请对以上处理结果进行评分"},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
                task_type=self._task_type,
            )
        except AuthenticationError as e:
            raise NonRetryableError(f"API 认证失败: {e}", cause=e)
        except (APIError, APIConnectionError, RateLimitError) as e:
            raise RetryableError(f"API 调用失败: {e}", cause=e)

        raw = response.choices[0].message.content or "{}"
        logger.info(f"[Reviewer] LLM 响应: {raw}")

        try:
            result = parse_json_response(raw)
        except json.JSONDecodeError as e:
            raise NonRetryableError(f"LLM 返回非法 JSON: {e}", cause=e)

        score = float(result.get("score", 0.7))
        # 确保 score 在 0-1 范围内
        score = max(0.0, min(1.0, score))
        dimensions = self._normalize_dimensions(result.get("dimensions"))
        issues = result.get("issues")
        if not isinstance(issues, list):
            issues = []
        clean_issues = [str(issue).strip() for issue in issues if str(issue).strip()]
        suggestion = str(result.get("suggestion") or "")
        feedback = str(result.get("feedback") or "")
        issue_type = self._resolve_issue_type(
            result.get("issue_type"),
            clean_issues,
            feedback,
            suggestion,
            content,
            processing_result,
        )
        retry_suppressed = issue_type in _NON_RETRYABLE_ISSUE_TYPES

        should_retry = False if retry_suppressed else bool(
            result.get("should_retry")
            or score < Settings().review_threshold
        )

        return {
            "score": score,
            "feedback": feedback,
            "dimensions": dimensions,
            "issues": clean_issues,
            "suggestion": suggestion,
            "should_retry": should_retry,
            "issue_type": issue_type,
            "retry_suppressed": retry_suppressed,
            "clarification_request": self._build_clarification_request(
                issue_type,
                clean_issues,
                suggestion,
                content,
            ),
        }

    @staticmethod
    def _normalize_dimensions(value: object) -> dict[str, float]:
        """标准化四维质检分，避免 LLM 缺字段导致前端展示不完整。"""
        raw = value if isinstance(value, dict) else {}
        max_scores = {
            "accuracy": 0.3,
            "feasibility": 0.3,
            "completeness": 0.2,
            "professionalism": 0.2,
        }
        normalized: dict[str, float] = {}
        for key, max_score in max_scores.items():
            try:
                score = float(raw.get(key, 0.0))
            except (TypeError, ValueError):
                score = 0.0
            normalized[key] = round(max(0.0, min(max_score, score)), 4)
        return normalized

    @classmethod
    def _resolve_issue_type(
        cls,
        raw_issue_type: object,
        issues: list[str],
        feedback: str,
        suggestion: str,
        content: str,
        processing_result: str,
    ) -> str:
        """识别问题类型，区分可返工修复与知识盲区/信息不足。"""
        normalized = cls._normalize_issue_type(raw_issue_type)
        if normalized != "none":
            return normalized

        review_text = " ".join([*issues, feedback, suggestion, content, processing_result])
        inferred = cls._infer_non_retryable_issue_type(review_text)
        if inferred:
            return inferred
        if issues:
            return "fixable"
        return "none"

    @staticmethod
    def _normalize_issue_type(value: object) -> str:
        """标准化 LLM 输出的问题类型，兼容中文/英文表述。"""
        raw = str(value or "").strip().lower()
        if raw in {"none", "fixable", "knowledge_gap", "needs_clarification", "out_of_scope"}:
            return raw
        if raw in {"knowledge gap", "knowledge-gap", "知识盲区", "知识库盲区", "知识库未覆盖"}:
            return "knowledge_gap"
        if raw in {"clarification", "need_clarification", "needs clarification", "信息不足", "需要补充"}:
            return "needs_clarification"
        if raw in {"out of scope", "out-of-scope", "超出范围", "范围外"}:
            return "out_of_scope"
        return "none"

    @staticmethod
    def _infer_non_retryable_issue_type(text: str) -> str | None:
        """从审核文本中推断不可通过重试解决的问题类型。"""
        lower_text = text.lower()
        knowledge_gap_keywords = (
            "知识库未覆盖",
            "知识库暂无",
            "知识库没有",
            "知识库盲区",
            "知识盲区",
            "未检索到",
            "没有检索到",
            "无相关知识",
            "现有知识库",
            "超出知识库",
            "not covered",
            "no relevant knowledge",
            "knowledge gap",
        )
        clarification_keywords = (
            "问题模糊",
            "描述不明确",
            "描述过于笼统",
            "信息不足",
            "必要信息不足",
            "无法判断",
            "无法确认",
            "需要用户补充",
            "请用户补充",
            "需用户补充",
            "need clarification",
            "needs clarification",
        )
        out_of_scope_keywords = (
            "超出支持范围",
            "不在支持范围",
            "out of scope",
        )
        if any(keyword in lower_text for keyword in knowledge_gap_keywords):
            return "knowledge_gap"
        if any(keyword in lower_text for keyword in clarification_keywords):
            return "needs_clarification"
        if any(keyword in lower_text for keyword in out_of_scope_keywords):
            return "out_of_scope"
        return None

    @staticmethod
    def _build_clarification_request(
        issue_type: str,
        issues: list[str],
        suggestion: str,
        content: str,
    ) -> str:
        """为知识盲区/信息不足构造面向用户的补充说明。"""
        if issue_type == "knowledge_gap":
            detail = suggestion or "请补充具体业务场景、系统名称、接口类型、报错信息或期望操作结果。"
            return f"当前知识库未覆盖该问题的可靠处理方案。{detail}"
        if issue_type == "needs_clarification":
            detail = suggestion or "请补充具体现象、操作步骤、时间点、账号环境或相关截图。"
            return f"当前问题描述还不够明确，暂时无法生成可靠处理方案。{detail}"
        if issue_type == "out_of_scope":
            detail = suggestion or "请确认该问题是否属于当前系统支持范围，或补充关联业务背景。"
            return f"该问题可能超出当前 Agent 可自动处理范围。{detail}"
        if issues:
            return suggestion or f"请补充以下信息：{'；'.join(issues)}"
        return suggestion or f"请补充与「{content[:40]}」相关的更多上下文。"

    @staticmethod
    def _fallback_review() -> dict:
        """LLM 调用失败时的降级审核。

        Returns:
            默认审核结果字典
        """
        return {
            "score": 0.7,
            "feedback": "LLM 审核不可用，使用默认评分",
            "dimensions": {
                "accuracy": 0.21,
                "feasibility": 0.21,
                "completeness": 0.14,
                "professionalism": 0.14,
            },
            "issues": ["Reviewer LLM 不可用，未完成深度质检"],
            "suggestion": "建议人工复核关键结论后再发送",
            "should_retry": False,
            "issue_type": "none",
            "retry_suppressed": False,
            "clarification_request": "",
        }

    @staticmethod
    def create_from_settings() -> "ReviewerAgent":
        """从 Settings 创建 ReviewerAgent 实例。

        Returns:
            配置好的 ReviewerAgent 实例
        """
        settings = Settings()
        return ReviewerAgent(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )


# 模块级降级注册
fallback_registry.register("reviewer.review", ReviewerAgent._fallback_review)
