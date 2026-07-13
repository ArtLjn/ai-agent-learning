import pytest
from unittest.mock import AsyncMock, MagicMock

from src.multi_agent_system.agents.processor_react import ReActProcessorAgent
from src.multi_agent_system.core.tool_base import ToolBase, ToolRegistry
from src.multi_agent_system.tools.rag_client import RagChunk, RagServiceUnavailable
from pydantic import BaseModel, Field


class MockSearchParams(BaseModel):
    query: str = Field(description="Search query")


class MockSearchTool(ToolBase):
    name = "search_knowledge"
    description = "Search knowledge base"
    params_model = MockSearchParams

    async def execute(self, query: str) -> str:
        return f"Knowledge about {query}"

    async def fallback(self, query: str) -> str:
        return "Knowledge base unavailable"


class MapKnowledgeSearchTool(ToolBase):
    name = "search_knowledge"
    description = "Search knowledge base"
    params_model = MockSearchParams

    async def execute(self, query: str) -> str:
        return (
            "检索到以下知识片段：1. 标题: 地图服务；分类: integration-map；相似度: 0.747 "
            "内容: 集成高德、百度或腾讯地图 SDK 时，应检查 MAP_KEY、MAP_SECRET、"
            "包名、Bundle ID、域名 Referer 白名单、服务开通状态和接口返回码。"
        )

    async def fallback(self, query: str) -> str:
        return "Knowledge base unavailable"


class _CollectingSpan:
    """收集 RAG span 的 metadata，避免测试依赖真实数据库 trace。"""

    def __init__(self) -> None:
        self.metadata = {}
        self.output = {}
        self.status = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def set_metadata(self, data):
        self.metadata.update(data)

    def set_output(self, data):
        self.output.update(data)

    def set_status(self, status):
        self.status = status


class _SuccessfulRagClient:
    async def retrieve(self, **kwargs):
        return [
            RagChunk(
                id="doc-1",
                content="登录失败请检查账号状态和统一认证服务。",
                score=0.81,
                metadata={"title": "登录手册", "category": "technical"},
            )
        ], {"actual_mode": "hybrid", "warning": None}

    async def rerank(self, **kwargs):
        return [
            RagChunk(
                id="doc-1",
                content="登录失败请检查账号状态和统一认证服务。",
                score=0.93,
                metadata={"title": "登录手册", "category": "technical"},
            )
        ]


class _FailingRerankRagClient(_SuccessfulRagClient):
    async def rerank(self, **kwargs):
        raise RagServiceUnavailable("rerank timeout")


@pytest.fixture
def mock_client():
    client = MagicMock()
    return client


@pytest.mark.asyncio
async def test_react_processor_runs_loop(mock_client):
    tool = MockSearchTool()
    registry = ToolRegistry()
    registry.register(tool)

    agent = ReActProcessorAgent(
        model="test-model",
        tool_registry=registry,
        client=mock_client,
    )

    # Mock LLM responses: first thinks, then calls tool, then answers
    responses = [
        # Iteration 1: Thought + Action
        "Thought: I need to search for information.\nAction: search_knowledge({\"query\": \"login issue\"})",
        # Iteration 2: Final Answer
        "Thought: I have enough information.\nFinal Answer: Please reset your password.",
    ]

    mock_client.chat_completions_create = AsyncMock(side_effect=[
        MagicMock(choices=[MagicMock(message=MagicMock(content=r))])
        for r in responses
    ])

    result = await agent.process("无法登录", "technical", "P1")

    assert "result" in result
    assert "references" in result
    assert "Knowledge about 无法登录" in result["references"]
    assert "Knowledge about login issue" in result["references"]
    assert mock_client.chat_completions_create.call_count == 2


@pytest.mark.asyncio
async def test_react_processor_keeps_parsed_references_when_no_tool(mock_client):
    """LLM 直接返回 JSON 时，应保留模型给出的 references。"""
    agent = ReActProcessorAgent(
        model="test-model",
        client=mock_client,
    )
    mock_client.chat_completions_create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content='{"result": "请按手册处理", "references": ["登录手册"]}'
                    )
                )
            ]
        )
    )

    result = await agent.process("无法登录", "technical", "P1")

    assert result["result"] == "请按手册处理"
    assert result["references"] == ["登录手册"]


@pytest.mark.asyncio
async def test_react_processor_prefetches_knowledge_for_technical_ticket(mock_client):
    """技术类工单应先检索知识库，避免完全依赖模型主动调用工具。"""
    tool = MockSearchTool()
    registry = ToolRegistry()
    registry.register(tool)
    agent = ReActProcessorAgent(
        model="test-model",
        tool_registry=registry,
        client=mock_client,
    )
    mock_client.chat_completions_create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content="Thought: enough.\nFinal Answer: 请根据知识库处理。"
                    )
                )
            ]
        )
    )

    result = await agent.process("ERR-5001 无法登录", "technical", "P1")

    assert result["references"] == ["Knowledge about ERR-5001 无法登录"]
    sent_messages = mock_client.chat_completions_create.call_args.kwargs["messages"]
    assert "Knowledge about ERR-5001 无法登录" in sent_messages[0]["content"]


@pytest.mark.asyncio
async def test_react_processor_prefetches_knowledge_for_coupon_inquiry(mock_client):
    """咨询类优惠券问题也应先检索知识库，避免直接生成泛化回答。"""
    tool = MockSearchTool()
    registry = ToolRegistry()
    registry.register(tool)
    agent = ReActProcessorAgent(
        model="test-model",
        tool_registry=registry,
        client=mock_client,
    )
    mock_client.chat_completions_create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content="Thought: enough.\nFinal Answer: 请按知识库规则使用优惠券。"
                    )
                )
            ]
        )
    )

    result = await agent.process("咨询一下平台优惠卷如何使用", "inquiry", "P3")

    assert result["references"] == ["Knowledge about 咨询一下平台优惠券如何使用"]
    sent_messages = mock_client.chat_completions_create.call_args.kwargs["messages"]
    assert "Knowledge about 咨询一下平台优惠券如何使用" in sent_messages[0]["content"]


@pytest.mark.asyncio
async def test_react_processor_keeps_prefetched_references_when_json_has_empty_list(mock_client):
    """模型 JSON 返回空 references 时，不能覆盖预检索到的知识库引用。"""
    tool = MockSearchTool()
    registry = ToolRegistry()
    registry.register(tool)
    agent = ReActProcessorAgent(
        model="test-model",
        tool_registry=registry,
        client=mock_client,
    )
    mock_client.chat_completions_create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content='{"result": "请清理缓存并检查认证服务", "references": []}'
                    )
                )
            ]
        )
    )

    result = await agent.process("ERR-5001 无法登录", "technical", "P1")

    assert result["result"] == "请清理缓存并检查认证服务"
    assert result["references"] == ["Knowledge about ERR-5001 无法登录"]


@pytest.mark.asyncio
async def test_react_processor_fallback_uses_prefetched_knowledge(mock_client):
    """处理模型不可用时，应基于知识库检索结果生成降级答复。"""
    tool = MockSearchTool()
    registry = ToolRegistry()
    registry.register(tool)
    agent = ReActProcessorAgent(
        model="test-model",
        tool_registry=registry,
        client=mock_client,
    )
    mock_client.chat_completions_create = AsyncMock(side_effect=Exception("LLM 502"))

    result = await agent.process("咨询一下平台优惠卷如何使用", "inquiry", "P3")

    assert "Knowledge about 咨询一下平台优惠券如何使用" in result["result"]
    assert result["references"] == ["Knowledge about 咨询一下平台优惠券如何使用"]


@pytest.mark.asyncio
async def test_react_processor_fallback_with_related_knowledge_is_not_unknown(mock_client):
    """有相关知识库命中时，降级答复应给参考建议，而不是直接说暂无答案。"""
    tool = MapKnowledgeSearchTool()
    registry = ToolRegistry()
    registry.register(tool)
    agent = ReActProcessorAgent(
        model="test-model",
        tool_registry=registry,
        client=mock_client,
    )
    mock_client.chat_completions_create = AsyncMock(side_effect=Exception("LLM 502"))

    result = await agent.process("咨询高德地图SDK配置及白名单规则", "inquiry", "P3")

    assert "可以先按以下方向核对" in result["result"]
    assert "高德" in result["result"]
    assert "白名单" in result["result"]
    assert "如仍无法确认" in result["result"]
    assert "知识库命中" not in result["result"]
    assert "知识库参考" not in result["result"]
    assert "相似度" not in result["result"]
    assert "置信度" not in result["result"]
    assert "人工确认" not in result["result"]
    assert "地图服务" not in result["result"]
    assert "知识库暂时没有收录该问题的明确答案" not in result["result"]


@pytest.mark.asyncio
async def test_react_processor_accepts_json_final_answer(mock_client):
    """模型把 ReAct 输出包进 JSON 时，应识别 Final Answer 并结束循环。"""
    tool = MockSearchTool()
    registry = ToolRegistry()
    registry.register(tool)
    agent = ReActProcessorAgent(
        model="test-model",
        tool_registry=registry,
        client=mock_client,
    )
    mock_client.chat_completions_create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content='{"Thought": "已有知识库上下文", "Final Answer": "请在结算页选择可用券。"}'
                    )
                )
            ]
        )
    )

    result = await agent.process("咨询一下平台优惠卷如何使用", "inquiry", "P3")

    assert result["result"] == "请在结算页选择可用券。"
    assert mock_client.chat_completions_create.call_count == 1


@pytest.mark.asyncio
async def test_react_processor_executes_json_action(mock_client):
    """模型返回 JSON 格式 Action 时，应执行工具而不是空转。"""
    tool = MockSearchTool()
    registry = ToolRegistry()
    registry.register(tool)
    agent = ReActProcessorAgent(
        model="test-model",
        tool_registry=registry,
        client=mock_client,
    )
    mock_client.chat_completions_create = AsyncMock(side_effect=[
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=(
                            '{"Thought": "需要补充平台能力概览", '
                            '"Action": {"tool": "search_knowledge", '
                            '"params": {"query": "平台能力概览"}}}'
                        )
                    )
                )
            ]
        ),
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content='{"Final Answer": "平台提供工单、知识库和数据分析能力。"}'
                    )
                )
            ]
        ),
    ])

    result = await agent.process("平台提供哪些能力", "inquiry", "P3")

    assert result["result"] == "平台提供工单、知识库和数据分析能力。"
    assert "Knowledge about 平台能力概览" in result["references"]


@pytest.mark.asyncio
async def test_react_processor_extracts_final_answer_from_json_like_text(mock_client):
    """模型把 JSON Final Answer 包在代码块中时，也应结束 ReAct 循环。"""
    agent = ReActProcessorAgent(
        model="test-model",
        client=mock_client,
    )
    mock_client.chat_completions_create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=(
                            '```json\n'
                            '{"Thought": "已有完整方案", '
                            '"Final Answer": "**处理建议**\\n1. 检查 Nginx upstream timed out\\n2. 重启异常后端节点"}'
                            '\n```'
                        )
                    )
                )
            ]
        )
    )

    result = await agent.process("后台一直 504", "technical", "P1")

    assert result["result"].startswith("**处理建议**")
    assert mock_client.chat_completions_create.call_count == 1


@pytest.mark.asyncio
async def test_react_processor_extracts_final_answer_from_broken_json_like_text(mock_client):
    """模型输出非严格 JSON 但包含 Final Answer 时，不应继续空转。"""
    agent = ReActProcessorAgent(
        model="test-model",
        client=mock_client,
    )
    mock_client.chat_completions_create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=(
                            '{\n'
                            '  "Thought": "已有知识库上下文，可以直接答复",\n'
                            '  "Observation": "{"broken": "nested"}",\n'
                            '  "Final Answer": "您好，请通过 security@company.com 上报，禁止未经授权攻击。"\n'
                            '}'
                        )
                    )
                )
            ]
        )
    )

    result = await agent.process("我要攻击你们系统了", "inquiry", "P3")

    assert result["result"].startswith("您好，请通过 security@company.com")
    assert mock_client.chat_completions_create.call_count == 1


@pytest.mark.asyncio
async def test_react_processor_stops_after_repeated_no_action_responses(mock_client):
    """连续无工具、无最终答案的重复响应应快速收敛，避免跑满 ReAct 轮次。"""
    tool = MockSearchTool()
    registry = ToolRegistry()
    registry.register(tool)
    agent = ReActProcessorAgent(
        model="test-model",
        tool_registry=registry,
        client=mock_client,
        max_iterations=10,
    )
    mock_client.chat_completions_create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content='{"Thought": "已有知识库上下文，建议按平台能力说明答复。"}'
                    )
                )
            ]
        )
    )

    result = await agent.process("平台提供哪些能力", "inquiry", "P3")

    assert "问题较复杂" not in result["result"]
    assert "Knowledge about 平台提供哪些能力" in result["result"]
    assert mock_client.chat_completions_create.call_count <= 2


@pytest.mark.asyncio
async def test_react_processor_rag_success_records_retrieve_and_rerank_metadata(mock_client):
    """RAG 成功时应记录命中数、top score、检索模式和 rerank 元数据。"""
    agent = ReActProcessorAgent(
        model="test-model",
        client=mock_client,
        rag_client=_SuccessfulRagClient(),
    )
    span = _CollectingSpan()
    agent._get_tool_span = lambda *args, **kwargs: span

    result = await agent._prefetch_knowledge("无法登录", "technical")

    assert "登录失败请检查账号状态" in result
    rag_stats = span.metadata["rag_stats"]
    assert rag_stats["hit_count"] == 1
    assert rag_stats["top_score"] == 0.93
    assert rag_stats["retrieval_mode"] == "hybrid"
    assert rag_stats["retrieve_hit_count"] == 1
    assert rag_stats["retrieve_top_score"] == 0.81
    assert rag_stats["rerank_applied"] is True
    assert rag_stats["rerank_hit_count"] == 1


@pytest.mark.asyncio
async def test_react_processor_rerank_unavailable_degrades_to_empty_references(mock_client):
    """rerank 超时或不可用时，应降级为空引用并不中断工单处理。"""
    agent = ReActProcessorAgent(
        model="test-model",
        client=mock_client,
        rag_client=_FailingRerankRagClient(),
    )
    span = _CollectingSpan()
    agent._get_tool_span = lambda *args, **kwargs: span

    result = await agent._prefetch_knowledge("无法登录", "technical")

    assert result == ""
    rag_stats = span.metadata["rag_stats"]
    assert rag_stats["hit_count"] == 0
    assert rag_stats["retrieve_hit_count"] == 1
    assert rag_stats["rerank_applied"] is False
    assert rag_stats["rerank_error"] == "rerank timeout"
    assert rag_stats["degraded"] is True
