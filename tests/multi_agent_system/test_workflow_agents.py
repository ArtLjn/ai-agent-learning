"""Agent 单元测试。

通过 mock OpenAI 客户端测试 4 个 Agent 的核心逻辑，
覆盖正常路径、降级路径和边界条件。不依赖真实 LLM API。

注意：各 Agent 的 client 属性是 property（延迟初始化），
测试中通过直接设置 _client 私有属性来注入 mock。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.multi_agent_system.agents.classifier import ClassifierAgent
from src.multi_agent_system.agents.coordinator import CoordinatorAgent
from src.multi_agent_system.agents.processor import ProcessorAgent
from src.multi_agent_system.agents.reviewer import ReviewerAgent


def _make_mock_response(content_dict: dict) -> MagicMock:
    """构造 LLM API 的 mock 响应对象。"""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content=json.dumps(content_dict)))
    ]
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
# ClassifierAgent 测试
# ============================================================


class TestClassifierAgent:
    """分类 Agent 测试。"""

    @pytest.mark.asyncio
    async def test_classify_technical(self):
        """LLM 正常返回技术类分类。"""
        mock_response = _make_mock_response({
            "category": "technical",
            "priority": "P1",
            "reason": "系统崩溃属于技术问题",
        })
        mock_client = _make_mock_client(mock_response)

        agent = ClassifierAgent(model="test-model", api_key="fake")
        agent._client = mock_client

        result = await agent.classify("系统崩溃了")

        assert result["category"] == "technical"
        assert result["priority"] == "P1"
        assert "崩溃" in result["reason"]

    @pytest.mark.asyncio
    async def test_classify_fallback_on_llm_error(self):
        """LLM 调用失败时降级到关键词匹配。"""
        mock_client = _make_mock_client(side_effect=Exception("LLM 连接失败"))

        agent = ClassifierAgent(model="test-model", api_key="fake")
        agent._client = mock_client

        result = await agent.classify("我要退款")

        assert result["category"] == "billing"
        assert result["priority"] == "P2"

    @pytest.mark.asyncio
    async def test_classify_general_inquiry_uses_llm_before_local_rule(self):
        """通用咨询也应先走 LLM 结构化判断，不被本地规则截断。"""
        mock_response = _make_mock_response({
            "category": "inquiry",
            "priority": "P3",
            "risk_level": "low",
            "requires_human_review": False,
            "risk_reason": "",
            "confidence": 0.91,
            "reason": "优惠券使用规则咨询",
        })
        mock_client = _make_mock_client(mock_response)
        agent = ClassifierAgent(model="test-model", api_key="fake")
        agent._client = mock_client

        result = await agent.classify("咨询一下平台优惠卷如何使用")

        assert result["category"] == "inquiry"
        assert result["priority"] == "P3"
        assert result["confidence"] == 0.91
        assert "优惠券" in result["reason"]
        mock_client.chat_completions_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_classify_fallback_no_keyword_match(self):
        """LLM 失败且无关键词匹配时，默认为咨询类。"""
        mock_client = _make_mock_client(side_effect=Exception("LLM 连接失败"))

        agent = ClassifierAgent(model="test-model", api_key="fake")
        agent._client = mock_client

        result = await agent.classify("你好")

        assert result["category"] == "inquiry"
        assert result["priority"] == "P3"

    @pytest.mark.asyncio
    async def test_classify_invalid_category_fallback(self):
        """LLM 返回非法分类时降级为 inquiry。"""
        mock_response = _make_mock_response({
            "category": "unknown_category",
            "priority": "P2",
            "reason": "测试非法分类",
        })
        mock_client = _make_mock_client(mock_response)

        agent = ClassifierAgent(model="test-model", api_key="fake")
        agent._client = mock_client

        result = await agent.classify("测试内容")

        assert result["category"] == "inquiry"

    @pytest.mark.asyncio
    async def test_classify_invalid_priority_fallback(self):
        """LLM 返回非法优先级时降级为 P3。"""
        mock_response = _make_mock_response({
            "category": "technical",
            "priority": "P99",
            "reason": "测试非法优先级",
        })
        mock_client = _make_mock_client(mock_response)

        agent = ClassifierAgent(model="test-model", api_key="fake")
        agent._client = mock_client

        result = await agent.classify("测试内容")

        assert result["priority"] == "P3"


# ============================================================
# ProcessorAgent 测试
# ============================================================


class TestProcessorAgent:
    """处理 Agent 测试。"""

    @pytest.fixture
    def mock_knowledge_tool(self):
        """创建 mock 的知识库检索工具。"""
        tool = MagicMock()
        tool.search = MagicMock(return_value=[
            {"content": "参考内容1", "score": 0.9},
            {"content": "参考内容2", "score": 0.8},
        ])
        return tool

    @pytest.mark.asyncio
    async def test_process_with_knowledge(self, mock_knowledge_tool):
        """ReAct 循环中 LLM 直接返回结果。"""
        mock_client = MagicMock()
        mock_client.chat_completions_create = AsyncMock(side_effect=[
            MagicMock(choices=[MagicMock(message=MagicMock(
                content='Thought: 分析问题\nFinal Answer: 根据参考资料，建议执行以下步骤...'
            ))])
        ])

        agent = ProcessorAgent(
            model="test-model",
            knowledge_tool=mock_knowledge_tool,
            api_key="fake",
        )
        agent._client = mock_client

        result = await agent.process("系统报错", "technical", "P2")

        assert result["result"] is not None
        assert len(result["result"]) > 0

    @pytest.mark.asyncio
    async def test_process_fallback(self, mock_knowledge_tool):
        """LLM 失败时降级到占位处理。"""
        mock_client = _make_mock_client(side_effect=Exception("LLM 不可用"))

        agent = ProcessorAgent(
            model="test-model",
            knowledge_tool=mock_knowledge_tool,
            api_key="fake",
        )
        agent._client = mock_client

        result = await agent.process("系统报错", "technical", "P1")

        assert "技术问题" in result["result"]
        assert result["references"] == []

    @pytest.mark.asyncio
    async def test_process_knowledge_search_failure(self):
        """知识库检索失败时仍能正常处理。"""
        mock_knowledge_tool = MagicMock()
        mock_knowledge_tool.search = MagicMock(
            side_effect=Exception("知识库连接失败")
        )

        mock_response = _make_mock_response({
            "result": "已分析问题并生成解决方案",
            "references": [],
        })
        mock_client = _make_mock_client(mock_response)

        agent = ProcessorAgent(
            model="test-model",
            knowledge_tool=mock_knowledge_tool,
            api_key="fake",
        )
        agent._client = mock_client

        result = await agent.process("系统报错", "technical", "P2")

        assert result["result"] is not None

    def test_build_knowledge_context_empty(self):
        """ReActProcessorAgent 通过上下文管理器处理，无需 _build_knowledge_context。"""
        # ReAct 模式不再使用 _build_knowledge_context，
        # 工具结果直接嵌入 ReAct 循环对话
        assert not hasattr(ProcessorAgent, "_build_knowledge_context")

    def test_build_knowledge_context_with_refs(self):
        """ReActProcessorAgent 无需 _build_knowledge_context 方法。"""
        assert not hasattr(ProcessorAgent, "_build_knowledge_context")


# ============================================================
# ReviewerAgent 测试
# ============================================================


class TestReviewerAgent:
    """审核 Agent 测试。"""

    @pytest.mark.asyncio
    async def test_review_high_score(self):
        """高质量处理结果获得高分。"""
        mock_response = _make_mock_response({
            "score": 0.92,
            "feedback": "处理结果准确、完整，解决方案切实可行",
        })
        mock_client = _make_mock_client(mock_response)

        agent = ReviewerAgent(model="test-model", api_key="fake")
        agent._client = mock_client

        result = await agent.review("系统报错", "已排查并解决", "technical")

        assert result["score"] == 0.92
        assert "feedback" in result

    @pytest.mark.asyncio
    async def test_review_supports_legacy_format_prompt_override(self):
        """兼容 DB 中旧版 Python format 风格 Prompt，不因 JSON 示例触发 Jinja 解析错误。"""
        mock_response = _make_mock_response({
            "score": 0.88,
            "feedback": "审核通过",
            "should_retry": False,
        })
        mock_client = _make_mock_client(mock_response)

        agent = ReviewerAgent(model="test-model", api_key="fake")
        agent._client = mock_client
        agent.set_prompt_override(
            "原始工单内容：{content}\n"
            "工单分类：{category}\n"
            "处理结果：{processing_result}\n"
            "严格按 JSON 输出：\n"
            '{{"score": 0.85, "dimensions": {{"accuracy": 0.27}}, "should_retry": false}}'
        )

        result = await agent.review("请问公司叫啥", "您好，公司名称是云舟科技有限公司。", "inquiry")

        assert result["score"] == 0.88
        mock_client.chat_completions_create.assert_awaited_once()
        sent_prompt = mock_client.chat_completions_create.call_args.kwargs["messages"][0]["content"]
        assert "请问公司叫啥" in sent_prompt
        assert "云舟科技有限公司" in sent_prompt
        assert "{content}" not in sent_prompt
        assert '{{"score"' not in sent_prompt

    @pytest.mark.asyncio
    async def test_review_fallback(self):
        """LLM 失败时返回默认评分。"""
        mock_client = _make_mock_client(side_effect=Exception("LLM 不可用"))

        agent = ReviewerAgent(model="test-model", api_key="fake")
        agent._client = mock_client

        result = await agent.review("系统报错", "已处理", "technical")

        assert result["score"] == 0.7
        assert "默认" in result["feedback"]

    @pytest.mark.asyncio
    async def test_review_knowledge_grounded_result_still_uses_llm_quality_gate(self):
        """已引用知识库且分数达标时，Reviewer 建议不应强制返工。"""
        mock_response = _make_mock_response({
            "score": 0.86,
            "feedback": "答案引用了知识库，但还需要提醒用户确认券有效期。",
            "dimensions": {
                "accuracy": 0.25,
                "feasibility": 0.24,
                "completeness": 0.17,
                "professionalism": 0.20,
            },
            "issues": ["未提醒确认优惠券有效期"],
            "suggestion": "补充有效期和适用范围检查步骤",
            "should_retry": False,
        })
        mock_client = _make_mock_client(mock_response)

        agent = ReviewerAgent(model="test-model", api_key="fake")
        agent._client = mock_client

        result = await agent.review(
            "咨询一下平台优惠卷如何使用",
            "您好，已根据知识库整理：\n检索到以下知识片段：优惠券使用规则",
            "inquiry",
        )

        assert result["score"] == 0.86
        assert result["dimensions"]["accuracy"] == 0.25
        assert result["issues"] == ["未提醒确认优惠券有效期"]
        assert result["suggestion"] == "补充有效期和适用范围检查步骤"
        assert result["should_retry"] is False
        mock_client.chat_completions_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_review_blocks_result_when_issues_present(self):
        """Reviewer 明确判断需要返工时，即使分数达标也应打回。"""
        mock_response = _make_mock_response({
            "score": 0.85,
            "feedback": "整体可用，但缺少业务拒绝规则说明。",
            "dimensions": {
                "accuracy": 0.27,
                "feasibility": 0.26,
                "completeness": 0.16,
                "professionalism": 0.16,
            },
            "issues": ["缺少对具体业务拒绝规则的直接说明"],
            "suggestion": "补充试用版申请被拒的常见业务限制条件",
            "should_retry": True,
        })
        mock_client = _make_mock_client(mock_response)

        agent = ReviewerAgent(model="test-model", api_key="fake")
        agent._client = mock_client

        result = await agent.review("试用版申请失败咨询", "建议检查网络和浏览器缓存", "inquiry")

        assert result["score"] == 0.85
        assert result["should_retry"] is True

    @pytest.mark.asyncio
    async def test_review_suppresses_retry_for_knowledge_gap(self):
        """知识库盲区不是可重试质量问题，应停止打回并请求补充。"""
        mock_response = _make_mock_response({
            "score": 0.55,
            "feedback": "现有知识库未覆盖公司模型对接 Claude Code 的具体流程。",
            "dimensions": {
                "accuracy": 0.12,
                "feasibility": 0.12,
                "completeness": 0.08,
                "professionalism": 0.16,
            },
            "issues": ["知识库未覆盖公司模型对接 Claude Code 的具体步骤"],
            "suggestion": "告知用户知识库暂未覆盖，并请用户补充模型类型、接口协议和账号环境。",
            "should_retry": True,
        })
        mock_client = _make_mock_client(mock_response)

        agent = ReviewerAgent(model="test-model", api_key="fake")
        agent._client = mock_client

        result = await agent.review(
            "询问一下公司的模型如何进行对接 claude code",
            "建议查看模型接入文档并配置 API Key。",
            "inquiry",
        )

        assert result["issue_type"] == "knowledge_gap"
        assert result["retry_suppressed"] is True
        assert result["should_retry"] is False
        assert "知识库" in result["clarification_request"]

    @pytest.mark.asyncio
    async def test_review_score_clamped(self):
        """评分超出范围时被钳制到 0-1 之间。"""
        mock_response = _make_mock_response({
            "score": 1.5,
            "feedback": "超出范围测试",
        })
        mock_client = _make_mock_client(mock_response)

        agent = ReviewerAgent(model="test-model", api_key="fake")
        agent._client = mock_client

        result = await agent.review("测试", "处理结果", "inquiry")

        assert result["score"] <= 1.0

    def test_fallback_review_static(self):
        """静态降级审核方法返回正确结构。"""
        result = ReviewerAgent._fallback_review()

        assert "score" in result
        assert "feedback" in result
        assert result["score"] == 0.7


# ============================================================
# CoordinatorAgent 测试
# ============================================================


class TestCoordinatorAgent:
    """协调 Agent 测试。"""

    @pytest.fixture
    def mock_tools(self):
        """创建 mock 的通知和知识库工具。"""
        notification_tool = MagicMock()
        notification_tool.send = MagicMock(return_value={"status": "sent"})

        knowledge_tool = MagicMock()
        knowledge_tool.search = MagicMock(return_value=[])

        return notification_tool, knowledge_tool

    @pytest.mark.asyncio
    async def test_escalate(self, mock_tools):
        """升级工单成功。"""
        notification_tool, knowledge_tool = mock_tools

        mock_response = _make_mock_response({
            "escalation_summary": "用户投诉客服态度",
            "suggested_action": "安排高级客服介入",
            "assigned_team": "客户关系团队",
        })
        mock_client = _make_mock_client(mock_response)

        agent = CoordinatorAgent(
            model="test-model",
            notification_tool=notification_tool,
            knowledge_tool=knowledge_tool,
            api_key="fake",
        )
        agent._client = mock_client

        result = await agent.escalate("T001", "用户多次投诉")

        assert result["escalation_summary"] is not None
        assert result["suggested_action"] is not None
        notification_tool.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_escalate_fallback(self, mock_tools):
        """LLM 失败时升级工单走降级方案。"""
        notification_tool, knowledge_tool = mock_tools
        mock_client = _make_mock_client(side_effect=Exception("LLM 不可用"))

        agent = CoordinatorAgent(
            model="test-model",
            notification_tool=notification_tool,
            knowledge_tool=knowledge_tool,
            api_key="fake",
        )
        agent._client = mock_client

        result = await agent.escalate("T001", "紧急投诉")

        assert "T001" in result["escalation_summary"]
        assert result["assigned_team"] is not None
        notification_tool.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_failure(self, mock_tools):
        """处理失败工单。"""
        notification_tool, knowledge_tool = mock_tools

        mock_response = _make_mock_response({
            "failure_analysis": "处理超时导致失败",
            "recovery_suggestion": "建议重试",
            "requires_manual_review": True,
        })
        mock_client = _make_mock_client(mock_response)

        agent = CoordinatorAgent(
            model="test-model",
            notification_tool=notification_tool,
            knowledge_tool=knowledge_tool,
            api_key="fake",
        )
        agent._client = mock_client

        result = await agent.handle_failure("T001", "处理超时")

        assert result["failure_analysis"] is not None
        notification_tool.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_failure_fallback(self, mock_tools):
        """LLM 失败时处理失败工单走降级方案。"""
        notification_tool, knowledge_tool = mock_tools
        mock_client = _make_mock_client(side_effect=Exception("LLM 不可用"))

        agent = CoordinatorAgent(
            model="test-model",
            notification_tool=notification_tool,
            knowledge_tool=knowledge_tool,
            api_key="fake",
        )
        agent._client = mock_client

        result = await agent.handle_failure("T001", "未知错误")

        assert "T001" in result["failure_analysis"]
        assert result["requires_manual_review"] is True

    @pytest.mark.asyncio
    async def test_generate_report_empty(self, mock_tools):
        """空工单列表时返回提示信息。"""
        notification_tool, knowledge_tool = mock_tools

        agent = CoordinatorAgent(
            model="test-model",
            notification_tool=notification_tool,
            knowledge_tool=knowledge_tool,
            api_key="fake",
        )

        result = await agent.generate_report([])

        assert "无工单数据" in result

    def test_fallback_report(self):
        """降级报告生成包含基础统计信息。"""
        tickets = [
            {"category": "technical", "status": "completed", "review_score": 0.9},
            {"category": "billing", "status": "completed", "review_score": 0.8},
            {"category": "technical", "status": "failed"},
        ]
        result = CoordinatorAgent._fallback_report(tickets)

        assert "总计: 3 条" in result
        assert "technical" in result
