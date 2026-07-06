"""工单意图理解 Agent：将用户自然语言描述转换为结构化工单。"""

import json
import re
from typing import Any

from loguru import logger
from openai import APIConnectionError, APIError, AuthenticationError, RateLimitError

from src.multi_agent_system.config import Settings
from src.multi_agent_system.core import CachedLLMClient, track_agent_execution, with_retry
from src.multi_agent_system.core.exceptions import NonRetryableError, RetryableError
from src.multi_agent_system.core.json_parser import parse_json_response
from src.multi_agent_system.core.risk_policy import assess_ticket_risk
from src.multi_agent_system.models.ticket import TicketCategory, TicketPriority

__all__ = ["TicketIntentAgent"]

# D-02：从 prompts/intent.j2 加载（DB active 版本可覆盖）
from src.multi_agent_system.prompts import get_prompt_template

_INTENT_SYSTEM_PROMPT = get_prompt_template("intent")

_CATEGORY_LABELS = {
    TicketCategory.TECHNICAL.value: "技术支持",
    TicketCategory.BILLING.value: "账务问题",
    TicketCategory.COMPLAINT.value: "投诉建议",
    TicketCategory.INQUIRY.value: "咨询问询",
}

_PRIORITY_LABELS = {
    TicketPriority.P0.value: "P0 紧急",
    TicketPriority.P1.value: "P1 高",
    TicketPriority.P2.value: "P2 普通",
    TicketPriority.P3.value: "P3 低",
}

_VALID_CATEGORIES = {c.value for c in TicketCategory}
_VALID_PRIORITIES = {p.value for p in TicketPriority}

_CONTACT_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|1[3-9]\d{9}")
_TIME_RE = re.compile(
    r"(今天|昨天|前天|上午|下午|晚上|凌晨|中午|刚才|[0-2]?\d:[0-5]\d|"
    r"\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2})"
)

_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("billing", "账单|扣费|退款|发票|套餐|余额|付款|支付|收费|多扣"),
    ("complaint", "投诉|不满|差评|态度差|建议|体验差|服务差"),
    ("technical", "崩溃|报错|无法登录|登录失败|接口|504|500|403|超时|不可用|打不开|同步失败|数据丢失"),
    ("inquiry", "咨询|如何|怎么|入口|哪里|规则|能否|是否|导出|使用"),
]


class TicketIntentAgent:
    """将一段用户描述理解为可直接创建的结构化工单。"""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        task_type: str = "intent",
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
        return self._prompt_override or _INTENT_SYSTEM_PROMPT

    @property
    def client(self) -> CachedLLMClient:
        """延迟初始化 LLM 客户端。"""
        if self._client is None:
            settings = Settings()
            self._client = CachedLLMClient(
                api_key=self._api_key or settings.llm_api_key,
                base_url=self._base_url or settings.llm_base_url,
                model=self._model,
            )
        return self._client

    @track_agent_execution("ticket_intent")
    async def extract(self, content: str) -> dict[str, Any]:
        """提取自然语言工单意图，LLM 不可用时使用本地规则。"""
        cleaned = content.strip()
        if not cleaned:
            raise ValueError("工单描述不能为空")
        return await self._extract_by_llm(cleaned)

    @with_retry(
        max_retries=2,
        backoff_base=1.5,
        retryable_exceptions=(APIError, APIConnectionError, RateLimitError, RetryableError),
        fallback=lambda self, content: self.extract_by_fallback(content),
    )
    async def _extract_by_llm(self, content: str) -> dict[str, Any]:
        """通过 LLM 将自然语言描述转换为结构化工单。"""
        logger.info(f"[TicketIntent] 调用 LLM 模型: {self._model}, 内容长度: {len(content)}")
        try:
            response = await self.client.chat_completions_create(
                messages=[
                    {"role": "system", "content": self._effective_system_prompt()},
                    {"role": "user", "content": f"请理解并结构化以下工单描述：\n{content}"},
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
        logger.info(f"[TicketIntent] LLM 响应: {raw}")
        try:
            parsed = parse_json_response(raw)
        except json.JSONDecodeError as e:
            raise NonRetryableError(f"LLM 返回非法 JSON: {e}", cause=e)

        return self._normalize_result(parsed, content)

    @classmethod
    def extract_by_fallback(cls, content: str) -> dict[str, Any]:
        """本地规则兜底提取，保障无 LLM 时仍可创建工单。"""
        category = cls._guess_category(content)
        priority = cls._guess_priority(content, category)
        impact = cls._guess_impact(content, priority)
        contact = cls._extract_contact(content)
        occurred_at = cls._extract_time(content)
        title = cls._build_title(content)
        expectation = cls._guess_expectation(content, category)

        return cls._normalize_result(
            {
                "title": title,
                "category": category,
                "priority": priority,
                "impact": impact,
                "expectation": expectation,
                "contact": contact,
                "occurred_at": occurred_at,
                "intent_kind": cls._guess_intent_kind(content, category),
                "requires_business_operation": cls._guess_requires_business_operation(content, category),
                "required_fields": cls._guess_required_fields(content, category),
                "can_auto_resolve": not cls._guess_requires_business_operation(content, category),
                "confidence": 0.58,
                "reason": "本地关键词规则提取",
            },
            content,
        )

    @staticmethod
    def _normalize_result(result: dict[str, Any], original_content: str) -> dict[str, Any]:
        """校验并补齐 Agent 结果，返回 API 可持久化的结构。"""
        category = str(result.get("category") or TicketCategory.INQUIRY.value)
        priority = str(result.get("priority") or TicketPriority.P3.value)
        if category not in _VALID_CATEGORIES:
            category = TicketCategory.INQUIRY.value
        if priority not in _VALID_PRIORITIES:
            priority = TicketPriority.P3.value
        risk = assess_ticket_risk(
            original_content,
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
        if risk.requires_human_review and risk.risk_level in {"high", "critical"}:
            category = TicketCategory.TECHNICAL.value
            if priority not in {TicketPriority.P0.value, TicketPriority.P1.value}:
                priority = TicketPriority.P1.value

        title = str(result.get("title") or TicketIntentAgent._build_title(original_content)).strip()
        impact = str(result.get("impact") or TicketIntentAgent._guess_impact(original_content, priority)).strip()
        expectation = str(result.get("expectation") or "").strip()
        contact = str(result.get("contact") or "").strip()
        occurred_at = str(result.get("occurred_at") or "").strip()
        intent_kind = str(result.get("intent_kind") or TicketIntentAgent._guess_intent_kind(original_content, category)).strip()
        requires_business_operation = bool(
            result.get("requires_business_operation")
            if result.get("requires_business_operation") is not None
            else TicketIntentAgent._guess_requires_business_operation(original_content, category)
        )
        required_fields = result.get("required_fields")
        if not isinstance(required_fields, list):
            required_fields = TicketIntentAgent._guess_required_fields(original_content, category)
        required_fields = [str(field) for field in required_fields if str(field).strip()]
        can_auto_resolve = bool(
            result.get("can_auto_resolve")
            if result.get("can_auto_resolve") is not None
            else not requires_business_operation
        )
        reason = str(result.get("reason") or "").strip()
        try:
            confidence = max(0.0, min(1.0, float(result.get("confidence", 0.7))))
        except (TypeError, ValueError):
            confidence = 0.7

        normalized = {
            "title": title[:60],
            "category": category,
            "priority": priority,
            "impact": impact or "仅本人受影响",
            "expectation": expectation,
            "contact": contact,
            "occurred_at": occurred_at,
            "intent_kind": intent_kind,
            "requires_business_operation": requires_business_operation,
            "required_fields": required_fields,
            "can_auto_resolve": can_auto_resolve,
            "risk_level": risk.risk_level,
            "requires_human_review": risk.requires_human_review,
            "risk_reason": risk.reason,
            "confidence": confidence,
            "reason": reason,
        }
        normalized["content"] = TicketIntentAgent._format_content(
            normalized,
            original_content.strip(),
        )
        return normalized

    @staticmethod
    def _format_content(result: dict[str, Any], original_content: str) -> str:
        """格式化为现有处理工作流可理解的工单正文。"""
        rows = [
            f"【问题标题】{result['title']}",
            f"【问题类型】{_CATEGORY_LABELS[result['category']]}",
            f"【紧急程度】{_PRIORITY_LABELS[result['priority']]}",
            f"【影响范围】{result['impact']}",
        ]
        if result["expectation"]:
            rows.append(f"【期望处理】{result['expectation']}")
        if result["occurred_at"]:
            rows.append(f"【发生时间】{result['occurred_at']}")
        if result["contact"]:
            rows.append(f"【联系方式】{result['contact']}")
        rows.append(f"【意图类型】{result['intent_kind']}")
        rows.append(f"【需业务操作】{'是' if result['requires_business_operation'] else '否'}")
        rows.append(f"【可自动闭环】{'是' if result['can_auto_resolve'] else '否'}")
        if result["required_fields"]:
            rows.append(f"【缺失字段】{', '.join(result['required_fields'])}")
        rows.append(f"【风险等级】{result['risk_level']}")
        rows.append(f"【需人工审核】{'是' if result['requires_human_review'] else '否'}")
        if result["requires_human_review"]:
            rows.append(f"【风险原因】{result['risk_reason']}")
        if result["reason"]:
            rows.append(f"【Agent判断】{result['reason']}，置信度 {result['confidence']:.2f}")
        rows.append(f"【原始描述】{original_content}")
        return "\n".join(rows)

    @staticmethod
    def _guess_category(content: str) -> str:
        if assess_ticket_risk(content).requires_human_review:
            return TicketCategory.TECHNICAL.value
        for category, pattern in _CATEGORY_KEYWORDS:
            if re.search(pattern, content, flags=re.IGNORECASE):
                return category
        return TicketCategory.INQUIRY.value

    @staticmethod
    def _guess_priority(content: str, category: str) -> str:
        if re.search(r"核心业务不可用|完全不可用|全部用户|大规模|数据丢失|无法访问", content):
            return TicketPriority.P0.value
        if assess_ticket_risk(content, category=category).requires_human_review:
            return TicketPriority.P1.value
        if re.search(r"紧急|多人|部分用户|扣费|多扣|投诉|无法登录|504|资金", content):
            return TicketPriority.P1.value
        if category in {TicketCategory.TECHNICAL.value, TicketCategory.BILLING.value}:
            return TicketPriority.P2.value
        return TicketPriority.P3.value

    @staticmethod
    def _guess_impact(content: str, priority: str) -> str:
        if "核心业务不可用" in content:
            return "核心业务不可用"
        if re.search(r"全部用户|所有用户|大规模", content):
            return "全部用户受影响"
        if re.search(r"部分用户|多人|团队|业务人员", content):
            return "部分用户受影响"
        if priority == TicketPriority.P0.value:
            return "核心业务不可用"
        return "仅本人受影响"

    @staticmethod
    def _extract_contact(content: str) -> str:
        match = _CONTACT_RE.search(content)
        return match.group(0) if match else ""

    @staticmethod
    def _extract_time(content: str) -> str:
        matches = _TIME_RE.findall(content)
        return " ".join(dict.fromkeys(matches[:3]))

    @staticmethod
    def _build_title(content: str) -> str:
        first_line = content.strip().splitlines()[0]
        title = re.split(r"[。！？!?，,\n]", first_line, maxsplit=1)[0].strip()
        return title[:30] or "用户提交的新工单"

    @staticmethod
    def _guess_expectation(content: str, category: str) -> str:
        if "退款" in content:
            return "请协助核对并处理退款"
        if re.search(r"恢复|修复|解决", content):
            return "请尽快定位并恢复服务"
        if category == TicketCategory.INQUIRY.value:
            return "请告知具体处理方式"
        return ""

    @staticmethod
    def _guess_intent_kind(content: str, category: str) -> str:
        if category == TicketCategory.COMPLAINT.value:
            return "complaint"
        if re.search(r"怎么|如何|规则|多久到账|能否|是否|入口|使用", content):
            return "knowledge_question"
        if TicketIntentAgent._guess_requires_business_operation(content, category):
            return "business_action"
        if category == TicketCategory.TECHNICAL.value:
            return "incident"
        return "knowledge_question"

    @staticmethod
    def _guess_requires_business_operation(content: str, category: str) -> bool:
        if category not in {TicketCategory.BILLING.value, TicketCategory.COMPLAINT.value}:
            return False
        if re.search(r"退款|退费|扣款|扣费|多扣|重复|核查|核对|处理|补偿", content, re.IGNORECASE):
            return not re.search(r"规则|流程|多久到账|怎么|如何", content, re.IGNORECASE)
        return category == TicketCategory.COMPLAINT.value

    @staticmethod
    def _guess_required_fields(content: str, category: str) -> list[str]:
        if not TicketIntentAgent._guess_requires_business_operation(content, category):
            return []
        missing: list[str] = []
        if not re.search(r"(订单号|订单ID|order[_ -]?id|20\d{10,}|[A-Z]{2,}-?\d{6,})", content, re.IGNORECASE):
            missing.append("order_id")
        if re.search(r"支付|扣款|扣费|退款|退费", content) and not re.search(r"(流水|凭证|截图|交易号|支付单号)", content):
            missing.append("payment_record")
        return missing

    @staticmethod
    def create_from_settings() -> "TicketIntentAgent":
        """从 Settings 创建 TicketIntentAgent。"""
        settings = Settings()
        return TicketIntentAgent(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            task_type="intent",
        )
