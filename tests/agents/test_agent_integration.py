"""Agent 重试/降级机制集成测试。

验证各 Agent 类的 @with_retry 装饰器和 FallbackRegistry 集成行为：
1. RetryableError 触发重试
2. NonRetryableError 跳过重试直接降级
3. 降级回调正确触发
4. Settings 值覆盖 graph.py 中的硬编码常量

注意：各 Agent 的 client 属性是 property（延迟初始化），
测试中通过直接设置 _client 私有属性来注入 mock。
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.multi_agent_system.core import fallback_registry


def _make_mock_response(content_dict: dict) -> MagicMock:
    """构造 LLM API 的 mock 响应对象。"""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content=json.dumps(content_dict)))
    ]
    return mock_response


def _make_mock_response_text(text: str) -> MagicMock:
    """构造返回纯文本的 mock 响应对象。"""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=text))]
    return mock_response


def _make_mock_client(response: MagicMock | None = None, side_effect=None) -> AsyncMock:
    """构造 mock 的 CachedLLMClient（chat_completions_create 方法）。"""
    mock_client = AsyncMock()
    if side_effect:
        mock_client.chat_completions_create = AsyncMock(side_effect=side_effect)
    elif response:
        mock_client.chat_completions_create = AsyncMock(return_value=response)
    return mock_client


# ============================================================
# ClassifierAgent 集成测试
# ============================================================


class TestClassifierAgentRetry:
    """ClassifierAgent 重试/降级集成测试。"""

    @pytest.mark.asyncio
    async def test_classify_success_without_retry(self) -> None:
        """LLM 正常返回时，classify 直接返回结果，不触发重试。"""
        from src.multi_agent_system.agents.classifier import ClassifierAgent

        agent = ClassifierAgent(model="test-model")
        mock_response = _make_mock_response({
            "category": "technical",
            "priority": "P1",
            "reason": "系统崩溃",
        })
        agent._client = _make_mock_client(mock_response)

        result = await agent.classify("系统崩溃了")

        assert result["category"] == "technical"
        assert result["priority"] == "P1"

    @pytest.mark.asyncio
    async def test_classify_retryable_error_triggers_fallback(self) -> None:
        """LLM 调用抛出可重试异常时，重试耗尽后触发降级回调。"""
        from openai import APIError

        from src.multi_agent_system.agents.classifier import ClassifierAgent

        agent = ClassifierAgent(model="test-model")
        agent._client = _make_mock_client(
            side_effect=APIError(message="server error", request=MagicMock(), body=None)
        )

        result = await agent.classify("系统崩溃了")

        # 降级返回关键词匹配结果
        assert result["category"] == "technical"
        assert result.get("fallback") is True

    @pytest.mark.asyncio
    async def test_classify_non_retryable_error_triggers_fallback(self) -> None:
        """认证失败（不可重试异常）直接触发降级，不重试。"""
        from openai import AuthenticationError

        from src.multi_agent_system.agents.classifier import ClassifierAgent

        agent = ClassifierAgent(model="test-model")
        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise AuthenticationError(
                message="invalid api key",
                response=MagicMock(),
                body=None,
            )

        agent._client = _make_mock_client(side_effect=side_effect)

        result = await agent.classify("退款问题")

        # 认证失败只调用一次（不重试），直接降级
        assert call_count == 1
        assert result["category"] == "billing"
        assert result.get("fallback") is True

    @pytest.mark.asyncio
    async def test_classify_json_decode_error_triggers_fallback(self) -> None:
        """LLM 返回非法 JSON 时触发降级。"""
        from src.multi_agent_system.agents.classifier import ClassifierAgent

        agent = ClassifierAgent(model="test-model")
        mock_response = _make_mock_response_text("not a json")
        agent._client = _make_mock_client(mock_response)

        result = await agent.classify("咨询问题")

        # JSON 解析失败触发降级
        assert result.get("fallback") is True

    @pytest.mark.asyncio
    async def test_classify_invalid_schema_uses_safe_defaults_and_route_reason(self) -> None:
        """LLM 输出枚举非法时，应落安全默认值并保留可解释路由原因。"""
        from src.multi_agent_system.agents.classifier import ClassifierAgent

        agent = ClassifierAgent(model="test-model")
        mock_response = _make_mock_response({
            "category": "unknown-category",
            "priority": "P9",
            "reason": "模型输出了非法枚举",
            "confidence": "bad-score",
        })
        agent._client = _make_mock_client(mock_response)

        result = await agent.classify("咨询一下报表入口在哪里")

        assert result["category"] == "inquiry"
        assert result["priority"] == "P3"
        assert result["confidence"] == 0.8
        assert result["route_reason"]

    @pytest.mark.asyncio
    async def test_classify_fallback_generates_p0_route_reason(self) -> None:
        """关键词兜底应识别 P0 并输出 route_reason。"""
        from openai import APIError

        from src.multi_agent_system.agents.classifier import ClassifierAgent

        agent = ClassifierAgent(model="test-model")
        agent._client = _make_mock_client(
            side_effect=APIError(message="server error", request=MagicMock(), body=None)
        )

        result = await agent.classify("核心业务不可用，全部用户无法登录")

        assert result["category"] == "technical"
        assert result["priority"] == "P0"
        assert result["route_reason"]

    def test_classify_fallback_without_keyword_returns_default_route_reason(self) -> None:
        """完全未命中关键词时，也应返回安全默认分类。"""
        from src.multi_agent_system.agents.classifier import ClassifierAgent

        result = ClassifierAgent._classify_by_fallback("帮我看一下这个情况")

        assert result["category"] == "inquiry"
        assert result["priority"] == "P3"
        assert result["route_reason"] == "默认咨询类低风险工单，进入自动处理流程"


# ============================================================
# ProcessorAgent 集成测试
# ============================================================


class TestProcessorAgentRetry:
    """ProcessorAgent 重试/降级集成测试。"""

    @pytest.mark.asyncio
    async def test_process_success_without_retry(self) -> None:
        """LLM 正常返回时，process 直接返回结果。"""
        from src.multi_agent_system.agents.processor import ProcessorAgent

        mock_knowledge_tool = MagicMock()
        mock_knowledge_tool.search.return_value = []

        agent = ProcessorAgent(model="test-model", knowledge_tool=mock_knowledge_tool)
        mock_response = _make_mock_response({"result": "已解决", "references": []})
        agent._client = _make_mock_client(mock_response)

        result = await agent.process("系统崩溃", "technical", "P1")

        assert result["result"] == "已解决"
        assert result["references"] == []

    @pytest.mark.asyncio
    async def test_process_retryable_error_triggers_fallback(self) -> None:
        """LLM 调用失败时，重试耗尽后触发降级回调。"""
        from openai import APIError

        from src.multi_agent_system.agents.processor import ProcessorAgent

        mock_knowledge_tool = MagicMock()
        mock_knowledge_tool.search.return_value = []

        agent = ProcessorAgent(model="test-model", knowledge_tool=mock_knowledge_tool)
        agent._client = _make_mock_client(
            side_effect=APIError(message="server error", request=MagicMock(), body=None)
        )

        result = await agent.process("系统崩溃", "technical", "P1")

        # 降级返回模板结果
        assert result.get("fallback") is True
        assert "排查" in result["result"]


# ============================================================
# ReviewerAgent 集成测试
# ============================================================


class TestReviewerAgentRetry:
    """ReviewerAgent 重试/降级集成测试。"""

    @pytest.mark.asyncio
    async def test_review_success_without_retry(self) -> None:
        """LLM 正常返回时，review 直接返回结果。"""
        from src.multi_agent_system.agents.reviewer import ReviewerAgent

        agent = ReviewerAgent(model="test-model")
        mock_response = _make_mock_response({"score": 0.9, "feedback": "处理很好"})
        agent._client = _make_mock_client(mock_response)

        result = await agent.review("系统崩溃", "已修复", "technical")

        assert result["score"] == 0.9
        assert result["feedback"] == "处理很好"

    @pytest.mark.asyncio
    async def test_review_retryable_error_triggers_fallback(self) -> None:
        """LLM 调用失败时，重试耗尽后触发降级回调（默认评分 0.7）。"""
        from openai import APIError

        from src.multi_agent_system.agents.reviewer import ReviewerAgent

        agent = ReviewerAgent(model="test-model")
        agent._client = _make_mock_client(
            side_effect=APIError(message="server error", request=MagicMock(), body=None)
        )

        result = await agent.review("系统崩溃", "已修复", "technical")

        # 降级返回默认评分
        assert result["score"] == 0.7
        assert result.get("fallback") is True


# ============================================================
# CoordinatorAgent 集成测试
# ============================================================


class TestCoordinatorAgentRetry:
    """CoordinatorAgent 重试/降级集成测试。"""

    @pytest.mark.asyncio
    async def test_escalate_success_without_retry(self) -> None:
        """LLM 正常返回时，escalate 直接返回升级分析。"""
        from src.multi_agent_system.agents.coordinator import CoordinatorAgent

        mock_notification = MagicMock()
        mock_knowledge = MagicMock()
        agent = CoordinatorAgent(
            model="test-model",
            notification_tool=mock_notification,
            knowledge_tool=mock_knowledge,
        )
        mock_response = _make_mock_response({
            "escalation_summary": "紧急",
            "suggested_action": "人工介入",
            "assigned_team": "技术团队",
        })
        agent._client = _make_mock_client(mock_response)

        result = await agent.escalate("TK-001", "系统宕机")

        assert result["escalation_summary"] == "紧急"
        mock_notification.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_escalate_failure_triggers_fallback(self) -> None:
        """LLM 升级分析失败时触发降级。"""
        from openai import APIError

        from src.multi_agent_system.agents.coordinator import CoordinatorAgent

        mock_notification = MagicMock()
        mock_knowledge = MagicMock()
        agent = CoordinatorAgent(
            model="test-model",
            notification_tool=mock_notification,
            knowledge_tool=mock_knowledge,
        )
        agent._client = _make_mock_client(
            side_effect=APIError(message="server error", request=MagicMock(), body=None)
        )

        result = await agent.escalate("TK-001", "系统宕机")

        # 降级返回默认升级信息
        assert result.get("fallback") is True
        assert "TK-001" in result["escalation_summary"]
        mock_notification.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_failure_success(self) -> None:
        """handle_failure 正常路径。"""
        from src.multi_agent_system.agents.coordinator import CoordinatorAgent

        mock_notification = MagicMock()
        mock_knowledge = MagicMock()
        agent = CoordinatorAgent(
            model="test-model",
            notification_tool=mock_notification,
            knowledge_tool=mock_knowledge,
        )
        mock_response = _make_mock_response({
            "failure_analysis": "超时",
            "recovery_suggestion": "重试",
            "requires_manual_review": False,
        })
        agent._client = _make_mock_client(mock_response)

        result = await agent.handle_failure("TK-001", "超时错误")

        assert result["failure_analysis"] == "超时"

    @pytest.mark.asyncio
    async def test_generate_report_success(self) -> None:
        """generate_report 正常路径。"""
        from src.multi_agent_system.agents.coordinator import CoordinatorAgent

        mock_notification = MagicMock()
        mock_knowledge = MagicMock()
        agent = CoordinatorAgent(
            model="test-model",
            notification_tool=mock_notification,
            knowledge_tool=mock_knowledge,
        )
        mock_response = _make_mock_response_text("工单报告：共处理 5 条")
        agent._client = _make_mock_client(mock_response)

        result = await agent.generate_report([{"category": "technical"}])

        assert "工单报告" in result

    @pytest.mark.asyncio
    async def test_generate_report_failure_triggers_fallback(self) -> None:
        """generate_report LLM 失败时触发降级。"""
        from openai import APIError

        from src.multi_agent_system.agents.coordinator import CoordinatorAgent

        mock_notification = MagicMock()
        mock_knowledge = MagicMock()
        agent = CoordinatorAgent(
            model="test-model",
            notification_tool=mock_notification,
            knowledge_tool=mock_knowledge,
        )
        agent._client = _make_mock_client(
            side_effect=APIError(message="server error", request=MagicMock(), body=None)
        )

        result = await agent.generate_report([{"category": "technical", "status": "completed"}])

        # 降级返回基础统计报告
        assert "总计: 1 条" in result

    @pytest.mark.asyncio
    async def test_generate_report_empty_returns_early(self) -> None:
        """generate_report 空列表直接返回，不调用 LLM。"""
        from src.multi_agent_system.agents.coordinator import CoordinatorAgent

        mock_notification = MagicMock()
        mock_knowledge = MagicMock()
        agent = CoordinatorAgent(
            model="test-model",
            notification_tool=mock_notification,
            knowledge_tool=mock_knowledge,
        )
        mock_client = _make_mock_client()
        agent._client = mock_client

        result = await agent.generate_report([])

        assert result == "无工单数据，无法生成报告。"
        mock_client.chat_completions_create.assert_not_called()


# ============================================================
# FallbackRegistry 注册验证
# ============================================================


class TestFallbackRegistryRegistration:
    """验证各 Agent 的降级函数已正确注册到全局注册表。"""

    def test_classifier_fallback_registered(self) -> None:
        """classifier.classify 降级函数已注册。"""
        fallbacks = fallback_registry.get("classifier.classify")
        assert len(fallbacks) >= 1

    def test_processor_fallback_registered(self) -> None:
        """processor.generate_solution 降级函数已注册。"""
        fallbacks = fallback_registry.get("processor.generate_solution")
        assert len(fallbacks) >= 1

    def test_reviewer_fallback_registered(self) -> None:
        """reviewer.review 降级函数已注册。"""
        fallbacks = fallback_registry.get("reviewer.review")
        assert len(fallbacks) >= 1

    def test_coordinator_escalate_fallback_registered(self) -> None:
        """coordinator.escalate 降级函数已注册。"""
        fallbacks = fallback_registry.get("coordinator.escalate")
        assert len(fallbacks) >= 1

    def test_coordinator_handle_failure_fallback_registered(self) -> None:
        """coordinator.handle_failure 降级函数已注册。"""
        fallbacks = fallback_registry.get("coordinator.handle_failure")
        assert len(fallbacks) >= 1

    def test_coordinator_generate_report_fallback_registered(self) -> None:
        """coordinator.generate_report 降级函数已注册。"""
        fallbacks = fallback_registry.get("coordinator.generate_report")
        assert len(fallbacks) >= 1


# ============================================================
# Graph.py Settings 集成测试
# ============================================================


class TestGraphSettingsIntegration:
    """验证 graph.py 使用 Settings 配置替代硬编码常量。"""

    def test_review_decision_uses_settings_threshold(self) -> None:
        """review_decision 使用 Settings.review_threshold 而非硬编码。"""
        from src.multi_agent_system.workflow.graph import review_decision

        # Settings 默认 review_threshold=0.7
        state_high = {"review_score": 0.8}
        assert review_decision(state_high) == "notify"

        state_low = {"review_score": 0.5}
        assert review_decision(state_low) == "retry_check"

    def test_retry_decision_uses_settings_max_retries(self) -> None:
        """retry_decision 使用 Settings.max_retries 而非硬编码。"""
        from src.multi_agent_system.workflow.graph import retry_decision

        # Settings 默认 max_retries=3
        state_below = {"retry_count": 2}
        assert retry_decision(state_below) == "process"

        state_at_limit = {"retry_count": 3}
        # Phase 2 起：达上限转入人工审核（trigger_type=review_failed），不再走 handle_failure
        assert retry_decision(state_at_limit) == "human_review_wait"

    @pytest.mark.asyncio
    async def test_classify_node_with_agent_no_try_except(self) -> None:
        """classify 节点在 Agent 可用时不再包含 try/except，
        直接信任 Agent 的重试/降级机制。"""
        from src.multi_agent_system.agents.classifier import ClassifierAgent
        from src.multi_agent_system.workflow.graph import classify

        agent = ClassifierAgent(model="test-model")
        mock_response = _make_mock_response({
            "category": "billing",
            "priority": "P2",
            "reason": "退款",
        })
        agent._client = _make_mock_client(mock_response)

        state = {
            "ticket_id": "TK-001",
            "content": "我要退款",
            "category": None,
            "priority": None,
            "processing_result": None,
            "review_score": None,
            "retry_count": 0,
            "status": "received",
            "messages": [],
            "error": None,
        }

        with patch("src.multi_agent_system.workflow.graph._classifier_agent", agent):
            result = await classify(state)

        assert result["category"] == "billing"
        assert result["status"] == "classifying"
