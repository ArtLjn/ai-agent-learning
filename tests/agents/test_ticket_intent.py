"""工单意图理解 Agent 测试。"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.multi_agent_system.agents.ticket_intent import TicketIntentAgent


def _make_mock_response(content_dict: dict) -> MagicMock:
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content=json.dumps(content_dict)))
    ]
    return mock_response


def _make_mock_client(response: MagicMock) -> AsyncMock:
    mock_client = AsyncMock()
    mock_client.chat_completions_create = AsyncMock(return_value=response)
    return mock_client


@pytest.mark.asyncio
async def test_extract_uses_llm_before_fallback() -> None:
    """创建工单时应优先使用 LLM 结构化意图，不被本地兜底截断。"""
    mock_response = _make_mock_response({
        "title": "VIP重复扣款",
        "category": "billing",
        "priority": "P1",
        "impact": "仅本人受影响",
        "expectation": "请核查并退款",
        "contact": "",
        "occurred_at": "",
        "risk_level": "medium",
        "requires_human_review": True,
        "risk_reason": "涉及真实账务处理，需要人工核查订单和支付流水",
        "confidence": 0.92,
        "reason": "用户请求处理重复扣款",
    })
    mock_client = _make_mock_client(mock_response)
    agent = TicketIntentAgent(model="test-model", api_key="fake")
    agent._client = mock_client

    result = await agent.extract("我充值vip由于网络原因导致重复扣款")

    assert result["category"] == "billing"
    assert result["priority"] == "P1"
    assert result["requires_human_review"] is True
    assert result["confidence"] == 0.92
    assert "本地关键词规则" not in result["content"]
    mock_client.chat_completions_create.assert_awaited_once()


def test_fallback_extracts_billing_refund_intent() -> None:
    """本地兜底能从自然语言中提取账务退款工单。"""
    result = TicketIntentAgent.extract_by_fallback(
        "上个月账单多扣了 200 元，请帮我退款，联系方式 finance@example.com"
    )

    assert result["category"] == "billing"
    assert result["priority"] == "P1"
    assert result["title"] == "上个月账单多扣了 200 元"
    assert result["contact"] == "finance@example.com"
    assert result["contact_context"] == "finance@example.com"
    assert result["missing_fields"] == result["required_fields"]
    assert "【原始描述】" in result["content"]


def test_fallback_returns_missing_fields_for_business_action() -> None:
    """涉及真实账务处理且缺少材料时，应返回缺失字段列表。"""
    result = TicketIntentAgent.extract_by_fallback(
        "上个月账单多扣了 200 元，请帮我退款"
    )

    assert result["category"] == "billing"
    assert result["requires_business_operation"] is True
    assert "order_id" in result["missing_fields"]
    assert result["required_fields"] == result["missing_fields"]


def test_fallback_marks_core_outage_as_p0() -> None:
    """核心业务不可用会被兜底为 P0 技术工单。"""
    result = TicketIntentAgent.extract_by_fallback(
        "今天上午 10:15 开始系统完全不可用，后台一直 504，全部用户无法登录"
    )

    assert result["category"] == "technical"
    assert result["priority"] == "P0"
    assert result["impact"] == "全部用户受影响"


def test_fallback_marks_security_report_as_p1_technical() -> None:
    """漏洞/安全风险上报应被兜底为 P1 技术工单。"""
    result = TicketIntentAgent.extract_by_fallback(
        "我发现你们支付功能有漏洞，支付之后跳转到一个不知名网页，疑似被劫持"
    )

    assert result["category"] == "technical"
    assert result["priority"] == "P1"
    assert "漏洞" in result["content"]
