"""5 个 P0 决策点埋点修复验证（详见 13 号设计文档第 11 节）。

测试覆盖：
- P0-1: traces.total_tokens 累加非 0（cached_client 已调 add_token_usage）
- P0-2: receive 节点内 load_user_context 独立 memory_call 子 span
- P0-3: RAG 检索 span.metadata.rag_stats 含 hit_count/top_score
- P0-4: retry_check span.metadata.decision 含 trigger/options/selection 五元组
- P0-5: classify span.metadata.decision.selection.reason 字段存在
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.multi_agent_system.core.database import DatabaseManager
from src.multi_agent_system.core.trace import TraceManager, current_trace_id
from src.multi_agent_system.models.ticket import TicketCategory, TicketPriority
from src.multi_agent_system.workflow import graph as graph_module


@pytest_asyncio.fixture
async def decision_setup(db_manager: DatabaseManager):
    """注入 trace_manager 到 graph 模块，供节点函数写 span。"""
    trace_manager = TraceManager(db_manager=db_manager)

    # 保存原值
    orig_trace = graph_module._trace_manager
    orig_active = graph_module._active_trace_id
    orig_classifier = graph_module._classifier_agent
    orig_memory = graph_module._memory_manager

    graph_module._trace_manager = trace_manager
    graph_module._classifier_agent = None
    graph_module._memory_manager = None

    yield trace_manager

    graph_module._trace_manager = orig_trace
    graph_module._active_trace_id = orig_active
    graph_module._classifier_agent = orig_classifier
    graph_module._memory_manager = orig_memory


async def _start_trace_for_ticket(
    trace_manager: TraceManager,
    db_manager: DatabaseManager,
    ticket_id: str,
    user_id: str | None = None,
) -> str:
    """创建 ticket + trace，返回 trace_id。"""
    await db_manager.save_ticket({
        "ticket_id": ticket_id,
        "user_id": user_id,
        "content": "测试工单内容",
        "status": "received",
    })
    trace_id = await trace_manager.start_trace(ticket_id)
    current_trace_id.set(trace_id)
    graph_module._active_trace_id = trace_id
    return trace_id


class TestP01TraceTotalTokensAccumulated:
    """P0-1: traces.total_tokens 累加非 0（修复前永远为 0）。"""

    @pytest.mark.asyncio
    async def test_trace_total_tokens_non_zero_after_llm_call(
        self, db_manager: DatabaseManager, decision_setup
    ) -> None:
        """模拟 LLM 调用后 traces.total_tokens 应累加 token 数。"""
        trace_id = await _start_trace_for_ticket(
            decision_setup, db_manager, "TK-P01"
        )
        # 直接调 TraceManager.add_token_usage 模拟 cached_client 行为
        await decision_setup.add_token_usage(trace_id, 200)

        trace = await db_manager.get_trace_by_ticket("TK-P01")
        assert trace is not None
        assert trace["total_tokens"] == 200  # 修复前为 0


class TestP02LoadUserContextSubSpan:
    """P0-2: receive 内 load_user_context 独立 memory_call 子 span。"""

    @pytest.mark.asyncio
    async def test_receive_creates_memory_load_context_subspan(
        self, db_manager: DatabaseManager, decision_setup
    ) -> None:
        """receive 节点调用后，spans 表应包含 memory_call 类型的 load_user_context 子 span。"""
        # mock memory_manager，确保 receive 走 load_user_context 分支
        mock_memory = MagicMock()
        mock_memory.load_user_context = AsyncMock(return_value={"recent_tickets": []})
        mock_memory.ensure_user = AsyncMock(return_value=None)
        graph_module._memory_manager = mock_memory

        trace_id = await _start_trace_for_ticket(
            decision_setup, db_manager, "TK-P02", user_id="user-001"
        )

        state = graph_module.create_initial_state("登录失败", ticket_id="TK-P02")
        state["user_id"] = "user-001"
        state["__trace_id__"] = trace_id
        await graph_module.receive(state)

        spans = await db_manager.get_spans_by_trace(trace_id)
        memory_spans = [
            s for s in spans
            if s["span_type"] == "memory_call" and s["name"] == "load_user_context"
        ]
        assert len(memory_spans) == 1, (
            f"expected 1 memory_call span, got {len(memory_spans)}; "
            f"all spans: {[s['name'] for s in spans]}"
        )
        # 验证子 span 的 parent 是 receive
        receive_spans = [s for s in spans if s["name"] == "receive"]
        assert len(receive_spans) == 1
        assert memory_spans[0]["parent_span_id"] == receive_spans[0]["span_id"]


class TestP03RagStatsInToolCallSpan:
    """P0-3: RAG 检索 span.metadata.rag_stats 含 hit_count/top_score。

    KnowledgeSearchToolAdapter.execute 已写入 rag_stats（v1.x 实现）。
    v2.0 ReActProcessorAgent 走 RagClient 路径时，_prefetch_via_rag_client 也写 rag_stats。
    本测试通过 KnowledgeSearchToolAdapter 验证既有埋点不破坏。
    """

    @pytest.mark.asyncio
    async def test_knowledge_tool_adapter_writes_rag_stats(
        self, db_manager: DatabaseManager, decision_setup
    ) -> None:
        """KnowledgeSearchToolAdapter.execute 写入 rag_stats 到 trace span。"""
        from src.multi_agent_system.tools.knowledge_tool_adapter import (
            KnowledgeSearchToolAdapter,
        )

        trace_id = await _start_trace_for_ticket(
            decision_setup, db_manager, "TK-P03"
        )

        # mock 知识库工具
        mock_knowledge = MagicMock()
        mock_knowledge.search.return_value = [
            {
                "content": "登录失败请检查账号密码",
                "score": 0.85,
                "metadata": {"title": "登录手册", "category": "technical"},
            },
            {
                "content": "网络异常请检查网络",
                "score": 0.72,
                "metadata": {"title": "网络手册", "category": "technical"},
            },
        ]

        adapter = KnowledgeSearchToolAdapter(knowledge_tool=mock_knowledge)
        result_text = await adapter.execute(query="登录失败", top_k=3, score_threshold=0.5)
        assert "登录失败请检查" in result_text

        spans = await db_manager.get_spans_by_trace(trace_id)
        tool_spans = [s for s in spans if s["name"] == "knowledge_search"]
        assert len(tool_spans) == 1
        metadata = json.loads(tool_spans[0]["metadata"]) if tool_spans[0]["metadata"] else {}
        rag_stats = metadata.get("rag_stats") or {}
        assert rag_stats.get("hit_count") == 2
        assert rag_stats.get("top_score") == 0.85


class TestP04RetryCheckDecisionSpan:
    """P0-4: retry_check 决策点 span.metadata.decision 含五元组。"""

    @pytest.mark.asyncio
    async def test_retry_check_span_has_boundary_decision(
        self, db_manager: DatabaseManager, decision_setup
    ) -> None:
        """retry_check 节点写 boundary 类型 decision（trigger/options/selection）。"""
        trace_id = await _start_trace_for_ticket(
            decision_setup, db_manager, "TK-P04"
        )

        # 构造一个 retry_count=1，max_retries=3 的 state（new_retry=2 < 3，selection=retry）
        state = graph_module.create_initial_state("测试", ticket_id="TK-P04")
        state["__trace_id__"] = trace_id
        state["retry_count"] = 1
        state["category"] = TicketCategory.TECHNICAL.value
        state["priority"] = TicketPriority.P2.value
        await graph_module.retry_check(state)

        spans = await db_manager.get_spans_by_trace(trace_id)
        retry_spans = [s for s in spans if s["name"] == "retry_check"]
        assert len(retry_spans) == 1
        metadata = json.loads(retry_spans[0]["metadata"]) if retry_spans[0]["metadata"] else {}
        decision = metadata.get("decision") or {}
        assert decision.get("decision_type") == "boundary"
        assert "trigger" in decision
        assert "options" in decision
        assert "selection" in decision
        # 验证 options 含 retry / escalate 两个候选
        option_values = [opt["value"] for opt in decision["options"]]
        assert "retry" in option_values
        assert "escalate" in option_values
        # retry_count=2 < max=3，selection 应为 retry
        assert decision["selection"]["value"] == "retry"


class TestP05ClassifyDecisionHasReason:
    """P0-5: classify span.metadata.decision.selection.reason 字段存在。"""

    @pytest.mark.asyncio
    async def test_classify_decision_carries_reason_field(
        self, db_manager: DatabaseManager, decision_setup
    ) -> None:
        """classify 节点写 routing 类型 decision，selection.reason 非空。"""
        # mock ClassifierAgent 输出含 reason
        mock_classifier = MagicMock()
        mock_classifier.classify = AsyncMock(return_value={
            "category": TicketCategory.TECHNICAL.value,
            "priority": TicketPriority.P1.value,
            "reason": "包含'登录失败'关键词，技术类信号最强",
            "confidence": 0.85,
            "requires_human_review": False,
            "risk_level": "low",
            "risk_reason": None,
        })
        graph_module._classifier_agent = mock_classifier

        trace_id = await _start_trace_for_ticket(
            decision_setup, db_manager, "TK-P05"
        )

        state = graph_module.create_initial_state("登录失败", ticket_id="TK-P05")
        state["__trace_id__"] = trace_id
        await graph_module.classify(state)

        spans = await db_manager.get_spans_by_trace(trace_id)
        classify_spans = [s for s in spans if s["name"] == "classify"]
        assert len(classify_spans) == 1
        metadata = json.loads(classify_spans[0]["metadata"]) if classify_spans[0]["metadata"] else {}
        decision = metadata.get("decision") or {}
        assert decision.get("decision_type") == "routing"
        selection = decision.get("selection") or {}
        assert selection.get("value") == TicketCategory.TECHNICAL.value
        assert selection.get("confidence") == 0.85
        # P0-5 关键：reason 字段非空
        assert selection.get("reason")
        assert "登录失败" in selection["reason"]


class TestReviewDecisionStructure:
    """P0 额外：review span.metadata.decision 含 quality_gate 五元组。"""

    @pytest.mark.asyncio
    async def test_review_span_has_quality_gate_decision(
        self, db_manager: DatabaseManager, decision_setup
    ) -> None:
        """review 节点写 quality_gate 类型 decision（pass/reject/options）。"""
        # mock ReviewerAgent 输出 score=0.6（低于阈值 0.7，应判 retry）
        mock_reviewer = MagicMock()
        mock_reviewer.review = AsyncMock(return_value={
            "score": 0.6,
            "should_retry": True,
            "retry_suppressed": False,
            "feedback": "需要补充",
            "issues": [],
            "suggestion": "补充细节",
            "dimensions": {},
            "issue_type": "fixable",
            "clarification_request": "",
        })
        graph_module._reviewer_agent = mock_reviewer

        trace_id = await _start_trace_for_ticket(
            decision_setup, db_manager, "TK-PREVIEW"
        )

        state = graph_module.create_initial_state("测试", ticket_id="TK-PREVIEW")
        state["__trace_id__"] = trace_id
        state["retry_count"] = 0
        state["processing_result"] = "处理方案"
        state["category"] = TicketCategory.TECHNICAL.value
        await graph_module.review(state)

        spans = await db_manager.get_spans_by_trace(trace_id)
        review_spans = [s for s in spans if s["name"] == "review"]
        assert len(review_spans) == 1
        metadata = json.loads(review_spans[0]["metadata"]) if review_spans[0]["metadata"] else {}
        decision = metadata.get("decision") or {}
        assert decision.get("decision_type") == "quality_gate"
        assert "trigger" in decision
        assert "options" in decision
        assert "selection" in decision
        assert "execution" in decision


class TestV22DecisionMetadata:
    """v2.2: route/process/human_review_wait 决策点应写完整 metadata。"""

    @pytest.mark.asyncio
    async def test_route_process_and_handoff_spans_have_execution_metadata(
        self, db_manager: DatabaseManager, decision_setup
    ) -> None:
        """关键 workflow 节点应写 trigger/options/selection/execution。"""
        trace_id = await _start_trace_for_ticket(
            decision_setup, db_manager, "TK-V22-TRACE"
        )

        state = graph_module.create_initial_state("核心业务不可用，全部用户无法登录", ticket_id="TK-V22-TRACE")
        state["__trace_id__"] = trace_id
        state["category"] = TicketCategory.TECHNICAL.value
        state["priority"] = TicketPriority.P0.value
        state["risk_level"] = "critical"
        state["requires_human_review"] = True
        state["risk_reason"] = "P0 核心业务不可用"
        await graph_module.route(state)
        await graph_module.human_review_wait(state)

        process_state = graph_module.create_initial_state("系统报错 ERR-5001", ticket_id="TK-V22-TRACE")
        process_state["__trace_id__"] = trace_id
        process_state["category"] = TicketCategory.TECHNICAL.value
        process_state["priority"] = TicketPriority.P2.value
        await graph_module.process(process_state)

        spans = await db_manager.get_spans_by_trace(trace_id)
        by_name = {span["name"]: span for span in spans}
        for name in ("route", "process", "human_review_wait"):
            metadata = json.loads(by_name[name]["metadata"]) if by_name[name]["metadata"] else {}
            decision = metadata.get("decision") or {}
            assert decision.get("trigger") is not None
            assert decision.get("options")
            assert decision.get("selection")
            assert decision.get("execution")
