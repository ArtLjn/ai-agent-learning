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


class _RecordingRagClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.retrieve_queries: list[str] = []
        self.rerank_queries: list[str] = []

    async def retrieve(self, **kwargs):
        self.retrieve_queries.append(kwargs["query"])
        return [
            RagChunk(
                id="doc-1",
                content=self.content,
                score=0.88,
                metadata={"title": "采购制度", "category": "inquiry"},
            )
        ], {"actual_mode": "hybrid", "warning": None}

    async def rerank(self, **kwargs):
        self.rerank_queries.append(kwargs["query"])
        return kwargs["chunks"]


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

    assert "可以先按以下信息处理" in result["result"]
    assert "云舟服务台" in result["result"]
    assert "高德" in result["result"]
    assert "白名单" in result["result"]
    assert "员工号" in result["result"]
    assert "审批单号" in result["result"]
    assert "可参考的资料要点" not in result["result"]
    assert "知识库" not in result["result"]
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


@pytest.mark.asyncio
async def test_react_processor_uses_llm_planned_query_for_rag(mock_client):
    """检索前先由 LLM 抽取员工诉求，rag-service 不应收到整段结构化工单。"""
    rag_client = _RecordingRagClient(
        "BuyDesk 采购办公用品和软件许可证需要提交用途、预算、数量和审批部门。"
    )
    agent = ReActProcessorAgent(
        model="test-model",
        client=mock_client,
        rag_client=rag_client,
    )
    mock_client.chat_completions_create = AsyncMock(side_effect=[
        MagicMock(choices=[MagicMock(message=MagicMock(content=(
            '{"retrieval_query": "云舟科技 BuyDesk 办公用品 软件采购 审批流程 证明材料", '
            '"answer_focus": ["审批流程", "证明材料", "加急审核"]}'
        )))]),
        MagicMock(choices=[MagicMock(message=MagicMock(content=(
            "Final Answer: 请在 BuyDesk 发起采购申请，并补充用途、预算、数量和审批部门。"
        )))]),
    ])

    await agent.process(
        "【问题标题】云舟 BuyDesk 采购制度咨询\n"
        "【问题类型】咨询问询\n"
        "【Agent判断】归类为 inquiry，置信度 0.95\n"
        "【原始描述】我是员工 2045，想咨询云舟科技的采购制度。"
        "请问通过 BuyDesk 平台申请办公用品和软件采购时，具体审批流程是怎样的？",
        "inquiry",
        "P3",
    )

    assert rag_client.retrieve_queries == [
        "云舟科技 BuyDesk 办公用品 软件采购 审批流程 证明材料"
    ]
    assert rag_client.rerank_queries == rag_client.retrieve_queries
    assert "Agent判断" not in rag_client.retrieve_queries[0]
    assert "置信度" not in rag_client.retrieve_queries[0]
    assert "员工 2045" not in rag_client.retrieve_queries[0]


@pytest.mark.asyncio
async def test_react_processor_composes_employee_answer_from_rag_when_react_stalls(mock_client):
    """ReAct 空转时，应让 LLM 基于命中文档组织答案，而不是粘贴知识库表格字段。"""
    rag_client = _RecordingRagClient(
        "采购申请\n"
        "| 员工说法 | 推荐关键词 |\n"
        "| --- | --- |\n"
        "| 买显示器 | 办公用品 固定资产 BuyDesk |\n"
        "通过 BuyDesk buy.yunzhou.example 发起。办公用品需填写用途、数量、预算；"
        "软件许可证需补充软件名称、使用人数、授权周期和部门负责人审批。"
    )
    agent = ReActProcessorAgent(
        model="test-model",
        client=mock_client,
        rag_client=rag_client,
        max_iterations=2,
    )
    mock_client.chat_completions_create = AsyncMock(side_effect=[
        MagicMock(choices=[MagicMock(message=MagicMock(content=(
            '{"retrieval_query": "云舟科技 BuyDesk 办公用品 软件许可证 采购审批 材料", '
            '"answer_focus": ["审批流程", "证明材料"]}'
        )))]),
        MagicMock(choices=[MagicMock(message=MagicMock(content='{"Thought": "已有知识库资料"}'))]),
        MagicMock(choices=[MagicMock(message=MagicMock(content='{"Thought": "继续整理"}'))]),
        MagicMock(choices=[MagicMock(message=MagicMock(content=(
            "您好，办公用品和软件许可证采购都需要在 BuyDesk 发起申请。办公用品请填写用途、"
            "数量和预算；软件许可证请补充软件名称、使用人数、授权周期，并提交部门负责人审批。"
        )))]),
    ])

    result = await agent.process(
        "我是员工 2045，想咨询云舟科技的采购制度。请问通过 BuyDesk 平台申请办公用品和软件采购时，"
        "具体审批流程是怎样的？需要提交哪些证明材料才能加快审核速度？",
        "inquiry",
        "P3",
    )

    assert "BuyDesk" in result["result"]
    assert "办公用品" in result["result"]
    assert "软件许可证" in result["result"]
    assert "员工说法" not in result["result"]
    assert "推荐关键词" not in result["result"]
    assert "---" not in result["result"]
    assert "可参考的资料要点" not in result["result"]


def test_related_knowledge_guidance_answers_company_name_directly(mock_client):
    """公司名称类简单咨询命中知识库时，应直接回答，不套旧排障模板。"""
    agent = ReActProcessorAgent(model="test-model", client=mock_client)
    reference = (
        "检索到以下知识片段：1. 标题: 知识库说明；分类: ticket_knowledge；相似度: 0.92 "
        "内容: 本知识库模拟公司为“云舟科技有限公司”。系统运维管理端负责查看 Trace、"
        "状态机、RAG、Prompt 和 Token。"
    )

    result = agent._build_related_knowledge_guidance(reference, content="你好公司叫啥呀")

    assert result == "您好，公司名称是云舟科技有限公司。"
    assert "Trace" not in result
    assert "RAG" not in result
    assert "Prompt" not in result
    assert "Token" not in result
    assert "Key/Secret" not in result


def test_related_knowledge_guidance_hides_document_source_from_employee(mock_client):
    """规则兜底也不能把文档名、分类和参考资料话术展示给员工。"""
    agent = ReActProcessorAgent(model="test-model", client=mock_client)
    reference = (
        "检索到以下知识片段：1. 标题: company-service-handbook.md；分类: inquiry；相似度: 0.91 "
        "内容: company-service-handbook.md 云舟科技员工服务总览 inquiry - 私人快递建议寄到个人住址,"
        "公司收发室优先处理公司业务件。 - 公司业务件收件人需写清部门、姓名、手机号和楼层。"
        " - P3:单个员工的一般咨询、入口指引、普通软件安装、常规权限申请。"
    )

    result = agent._build_related_knowledge_guidance(reference, content="请问公司叫啥")

    assert "company-service-handbook.md" not in result
    assert "参考" not in result
    assert "资料要点" not in result
    assert "知识库" not in result
    assert "inquiry" not in result
    assert "P3" not in result
    assert "私人快递" in result
    assert "收发室" in result


def test_normalize_knowledge_query_uses_employee_problem_not_agent_metadata(mock_client):
    """RAG 查询应使用员工问题本身，避免把 Agent 判断和置信度送入检索。"""
    agent = ReActProcessorAgent(model="test-model", client=mock_client)
    content = (
        "【问题标题】员工门户页面无法打开及报销制度咨询\n"
        "【问题类型】咨询问询\n"
        "【紧急程度】P3 低\n"
        "【影响范围】仅本人受影响\n"
        "【期望处理】获取备用入口或链接，了解差旅补贴标准\n"
        "【意图类型】knowledge_question\n"
        "【需业务操作】否\n"
        "【可自动闭环】是\n"
        "【风险等级】low\n"
        "【需人工审核】否\n"
        "【Agent判断】用户主要目的是查询报销制度和获取备用链接，属于功能咨询和知识问答。"
        "虽然提到页面打不开，但核心诉求是获取信息而非修复系统故障，且影响范围小，"
        "故归类为 inquiry 和 P3。，置信度 0.95\n"
        "【原始描述】我尝试访问云舟员工门户 portal.yunzhou.example 查询最新报销制度，"
        "但页面无法打开。请问是否有其他备用入口或链接？急需了解差旅补贴标准，谢谢协助。"
    )

    query = agent._normalize_knowledge_query(content)

    assert query.startswith("员工门户页面无法打开及报销制度咨询")
    assert "portal.yunzhou.example" in query
    assert "备用入口" in query
    assert "差旅补贴标准" in query
    assert "Agent判断" not in query
    assert "置信度" not in query
    assert "风险等级" not in query
    assert "需人工审核" not in query
    assert "knowledge_question" not in query
    assert "inquiry" not in query
