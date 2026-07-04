"""工单分类 Agent：分析工单内容，输出分类、优先级和路由建议。"""

import json

from loguru import logger
from openai import APIConnectionError, APIError, AuthenticationError, RateLimitError

from src.multi_agent_system.config import Settings
from src.multi_agent_system.core import CachedLLMClient, FallbackRegistry, fallback_registry, track_agent_execution, with_retry
from src.multi_agent_system.core.exceptions import NonRetryableError, RetryableError
from src.multi_agent_system.core.json_parser import parse_json_response
from src.multi_agent_system.core.risk_policy import assess_ticket_risk
from src.multi_agent_system.models.ticket import TicketCategory, TicketPriority

__all__ = ["ClassifierAgent"]

# 分类提示词
_CLASSIFIER_SYSTEM_PROMPT = """\
你是一个工单分类专家。请分析工单内容，输出分类和优先级。

分类规则：
- technical: 技术问题（崩溃、报错、无法登录、系统异常等）
- billing: 计费问题（退款、账单、扣费、付费异常等）
- complaint: 投诉（用户不满、服务差评、投诉要求等）
- inquiry: 咨询（使用方法、功能咨询、一般性问题等）

优先级规则：
- P0: 系统完全不可用、大规模故障、数据丢失
- P1: 核心功能故障、紧急投诉、资金异常、疑似安全漏洞或高危风险
- P2: 一般功能问题、普通计费问题
- P3: 咨询类、轻微问题

注意：category 只能选一个，不要组合多个分类。
请严格按照以下 JSON 格式输出，不要添加任何额外内容：
{"category": "technical 或 billing 或 complaint 或 inquiry（只选一个）", "priority": "P0 或 P1 或 P2 或 P3（只选一个）", "intent_kind": "knowledge_question 或 business_action 或 complaint 或 incident", "requires_business_operation": true 或 false, "required_fields": ["order_id", "payment_record", "user_id"] 中缺少则列出，没有则为空数组, "can_auto_resolve": true 或 false, "risk_level": "low 或 medium 或 high 或 critical", "requires_human_review": true 或 false, "risk_reason": "触发人工审核的风险原因，没有则为空字符串", "confidence": 0.0~1.0 的置信度, "reason": "分类和优先级判断的简要理由"}\
"""

# 关键词降级规则（与 graph.py 中的占位逻辑一致）
_FALLBACK_RULES: dict[str, tuple[str, str]] = {
    "崩溃": (TicketCategory.TECHNICAL.value, TicketPriority.P1.value),
    "报错": (TicketCategory.TECHNICAL.value, TicketPriority.P2.value),
    "无法登录": (TicketCategory.TECHNICAL.value, TicketPriority.P1.value),
    "退款": (TicketCategory.BILLING.value, TicketPriority.P2.value),
    "账单": (TicketCategory.BILLING.value, TicketPriority.P2.value),
    "扣费": (TicketCategory.BILLING.value, TicketPriority.P1.value),
    "投诉": (TicketCategory.COMPLAINT.value, TicketPriority.P1.value),
    "不满": (TicketCategory.COMPLAINT.value, TicketPriority.P1.value),
    "咨询": (TicketCategory.INQUIRY.value, TicketPriority.P3.value),
    "如何": (TicketCategory.INQUIRY.value, TicketPriority.P3.value),
    "怎么": (TicketCategory.INQUIRY.value, TicketPriority.P3.value),
}

# 合法的分类和优先级值
_VALID_CATEGORIES = {c.value for c in TicketCategory}
_VALID_PRIORITIES = {p.value for p in TicketPriority}


class ClassifierAgent:
    """工单分类 Agent：分析工单内容，输出分类 + 优先级 + 路由建议。

    通过 LLM 进行智能分类，支持降级到关键词匹配兜底。
    使用 OpenAI 兼容 API（支持 Ollama cloud 等兼容服务）。

    Args:
        model: 模型名称
        api_key: API 密钥，默认从环境变量读取
        base_url: API 基础地址，默认从 Settings 读取
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        task_type: str = "classify",
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._task_type = task_type
        self._client: CachedLLMClient | None = None
        self._prompt_override: str | None = None

    def set_prompt_override(self, template: str | None) -> None:
        """注入 DB 中的 active prompt 模板；传 None 还原代码默认。"""
        self._prompt_override = template

    def _effective_system_prompt(self) -> str:
        """当前生效的 system prompt（DB 覆盖 > 代码默认）。"""
        return self._prompt_override or _CLASSIFIER_SYSTEM_PROMPT

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

    @track_agent_execution("classifier")
    async def classify(self, content: str) -> dict:
        """分类工单，返回分类、优先级和理由。

        重试和降级由 @with_retry 装饰器统一处理。

        Args:
            content: 工单内容文本

        Returns:
            包含 category、priority、reason 的字典
        """
        return await self._classify_by_llm(content)

    @with_retry(
        max_retries=3,
        backoff_base=2.0,
        retryable_exceptions=(APIError, APIConnectionError, RateLimitError, RetryableError),
        fallback=lambda self, content: fallback_registry.execute("classifier.classify", content),
    )
    async def _classify_by_llm(self, content: str) -> dict:
        """通过 LLM 进行工单分类。

        由 @with_retry 装饰器统一处理重试和降级逻辑。

        Args:
            content: 工单内容文本

        Returns:
            分类结果字典

        Raises:
            RetryableError: OpenAI API 可重试错误
            NonRetryableError: 认证失败或 JSON 解析失败
        """
        logger.info(f"[Classifier] 调用 LLM 模型: {self._model}, 内容长度: {len(content)}")
        system_prompt = self._effective_system_prompt()
        logger.debug(f"[Classifier] 请求提示词:\n{system_prompt}\n用户内容: {content}")

        try:
            response = await self.client.chat_completions_create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请分类以下工单：\n{content}"},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
                task_type=self._task_type,
            )
        except AuthenticationError as e:
            raise NonRetryableError(f"API 认证失败: {e}", cause=e)
        except (APIError, APIConnectionError, RateLimitError) as e:
            raise RetryableError(f"API 调用失败: {e}", cause=e)

        raw = response.choices[0].message.content or "{}"
        logger.info(f"[Classifier] LLM 响应: {raw}")

        try:
            result = parse_json_response(raw)
        except json.JSONDecodeError as e:
            raise NonRetryableError(f"LLM 返回非法 JSON: {e}", cause=e)

        # 校验字段存在且值合法
        category = result.get("category", "")
        priority = result.get("priority", "")
        reason = result.get("reason", "")
        confidence_raw = result.get("confidence", 0.8)
        try:
            confidence = float(confidence_raw)
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.8

        if category not in _VALID_CATEGORIES:
            logger.warning(f"LLM 返回非法分类 '{category}'，降级到 inquiry")
            category = TicketCategory.INQUIRY.value

        if priority not in _VALID_PRIORITIES:
            logger.warning(f"LLM 返回非法优先级 '{priority}'，降级到 P3")
            priority = TicketPriority.P3.value

        risk = assess_ticket_risk(
            content,
            category=category,
            priority=priority,
            agent_risk={
                "risk_level": result.get("risk_level"),
                "requires_human_review": result.get("requires_human_review"),
                "risk_reason": result.get("risk_reason"),
                "requires_business_operation": result.get("requires_business_operation"),
                "can_auto_resolve": result.get("can_auto_resolve"),
                "required_fields": result.get("required_fields"),
            },
        )

        return {
            "category": category,
            "priority": priority,
            "risk_level": risk.risk_level,
            "requires_human_review": risk.requires_human_review,
            "risk_reason": risk.reason,
            "intent_kind": result.get("intent_kind"),
            "requires_business_operation": bool(result.get("requires_business_operation")),
            "required_fields": result.get("required_fields") if isinstance(result.get("required_fields"), list) else [],
            "can_auto_resolve": bool(result.get("can_auto_resolve")),
            "reason": reason,
            "confidence": confidence,
        }

    @staticmethod
    def _classify_by_fallback(content: str) -> dict:
        """关键词匹配降级分类。

        Args:
            content: 工单内容文本

        Returns:
            分类结果字典
        """
        rule_result = ClassifierAgent._match_keyword_rule(content, confidence=0.5)
        if rule_result is not None:
            return rule_result

        return {
            "category": TicketCategory.INQUIRY.value,
            "priority": TicketPriority.P3.value,
            "reason": "关键词匹配降级：未匹配到关键词，使用默认分类",
            "confidence": 0.3,
        }

    @staticmethod
    def _match_keyword_rule(content: str, confidence: float) -> dict | None:
        """按关键词规则分类，未匹配时返回 None。"""
        risk = assess_ticket_risk(content)
        if risk.requires_human_review:
            return {
                "category": TicketCategory.TECHNICAL.value,
                "priority": TicketPriority.P1.value,
                "risk_level": risk.risk_level,
                "requires_human_review": True,
                "risk_reason": risk.reason,
                "reason": "风险策略要求人工审核",
                "confidence": confidence,
            }
        for keyword, (category, priority) in _FALLBACK_RULES.items():
            if keyword in content:
                risk = assess_ticket_risk(
                    content,
                    category=category,
                    priority=priority,
                )
                return {
                    "category": category,
                    "priority": priority,
                    "risk_level": risk.risk_level,
                    "requires_human_review": risk.requires_human_review,
                    "risk_reason": risk.reason,
                    "reason": f"关键词规则匹配：匹配到 '{keyword}'",
                    "confidence": confidence,
                }
        return None

    @staticmethod
    def create_from_settings() -> "ClassifierAgent":
        """从 Settings 创建 ClassifierAgent 实例。

        Returns:
            配置好的 ClassifierAgent 实例
        """
        settings = Settings()
        return ClassifierAgent(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )


# 模块级降级注册：将关键词匹配降级函数注册到全局注册表
fallback_registry.register("classifier.classify", ClassifierAgent._classify_by_fallback)
