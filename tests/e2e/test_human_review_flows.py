"""人工审核端到端流程测试（Phase 5 - Task 9）。

覆盖 5 种端到端业务流程：

1. 投诉工单 → escalate → human_review_wait → 决策 approve → notify → complete
2. review 失败 3 次 → human_review_wait → 决策 rewrite → notify → complete
3. review 失败 3 次 → human_review_wait → 决策 reprocess → process → review → 通过
4. 工作流异常 → error_fallback → 决策 approve → 恢复
5. completed 工单 + satisfied=false → user_request 审核

不依赖真实 LLM 服务：所有 Agent 调用通过 mock 替换，工作流编排使用占位逻辑
或被 mock 的 CoordinatorAgent。

测试策略：跳过 API 层的 `asyncio.create_task` 异步竞态，直接调用
`workflow.astream` 与 `resume_from_human_decision`，在同一个事件循环内串行执行
全流程，再用 TestClient 验证 trace / stats / queue 等查询接口。
"""

import asyncio
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from fastapi.testclient import TestClient

from src.multi_agent_system.api.routes import router
from src.multi_agent_system.core.database import DatabaseManager
from src.multi_agent_system.tools.db_query import DBQueryTool
from tests.conftest import TEST_DATABASE_URL
from src.multi_agent_system.workflow import graph as graph_module
from src.multi_agent_system.workflow.graph import (
    build_ticket_graph,
    create_initial_state,
    resume_from_human_decision,
)

# ============================================================
# 公共 fixture
# ============================================================


def _make_mock_coordinator(recommended: str = "approve", confidence: float = 0.8) -> MagicMock:
    """构造一个 mock CoordinatorAgent，suggest_decision 返回固定建议。"""
    coord = MagicMock()
    coord.suggest_decision = AsyncMock(
        return_value={
            "recommended_decision": recommended,
            "confidence": confidence,
            "reasoning": "测试建议",
            "key_concerns": ["测试关注点"],
        }
    )
    return coord


@pytest.fixture
def e2e_app() -> FastAPI:
    """构建完整可用的 FastAPI 应用：内存 SQLite + DBQueryTool + 占位 workflow。

    workflow 注入由各测试自行调用 build_ticket_graph() 完成。
    db_manager 同时注入到 graph 模块级变量，供 apply_human_decision 持久化决策使用。
    """
    app = FastAPI()
    app.include_router(router, prefix="/api")

    db_manager = DatabaseManager(database_url=TEST_DATABASE_URL)
    asyncio.run(db_manager.initialize())
    asyncio.run(db_manager.truncate_all())

    app.state.db_manager = db_manager
    app.state.db_tool = DBQueryTool(db_manager=db_manager)
    # 同步注入 graph 模块级 db_manager，让 resume 子图的 apply_human_decision
    # 能正确持久化决策结果到 human_reviews 表
    graph_module._db_manager = db_manager
    # 占位状态，每个测试自行注入 workflow / coordinator
    app.state.workflow = None
    app.state.coordinator = None
    app.state.analytics_tool = MagicMock()
    app.state.analytics_tool.get_category_distribution = AsyncMock(return_value={})
    app.state.analytics_tool.get_priority_distribution = AsyncMock(return_value={})
    app.state.analytics_tool.get_resolution_stats = AsyncMock(return_value={})
    app.state.analytics_tool.get_daily_stats = AsyncMock(return_value=[])
    app.state.knowledge_tool = None
    app.state.memory_manager = None
    app.state.trace_manager = None
    app.state.tool_registry = None

    settings = MagicMock()
    settings.max_concurrency = 5
    settings.review_timeout_threshold = 1800
    settings.ai_suggestion_high_confidence_threshold = 0.7
    settings.review_threshold = 0.7
    app.state.settings = settings

    # require_role 依赖 request.session；装 SessionMiddleware 并启用演示模式
    # 让 reviews 路由的权限装饰器视为 admin 放行
    app.add_middleware(SessionMiddleware, secret_key="e2e-test-secret")
    os.environ["AUTH_ENABLED"] = "false"
    return app


@pytest.fixture
def client(e2e_app: FastAPI) -> TestClient:
    return TestClient(e2e_app)


@pytest.fixture(autouse=True)
def _reset_graph_module_state():
    """每个用例结束清理模块级变量，避免互相污染。"""
    yield
    graph_module._coordinator_agent = None
    graph_module._db_manager = None
    graph_module._trace_manager = None


# ============================================================
# 辅助函数
# ============================================================


async def _run_workflow_until_pending(
    app: FastAPI, ticket_id: str, state: dict[str, Any]
) -> dict[str, Any]:
    """驱动 workflow.astream 执行直到挂起，模拟 routes._run_workflow 的核心逻辑。"""
    workflow = app.state.workflow
    db_tool = app.state.db_tool
    current_state = dict(state)

    async for event in workflow.astream(state):
        for node_name, node_output in event.items():
            if not isinstance(node_output, dict):
                continue
            current_state.update(node_output)
            existing = await db_tool.get_ticket(ticket_id) or {}
            # 与 routes._run_workflow 一致：从 current_state 取核心字段
            merged = {
                **existing,
                **node_output,
                "ticket_id": ticket_id,
                "content": current_state.get("content", existing.get("content") or ""),
                "category": current_state.get("category") or existing.get("category"),
                "priority": current_state.get("priority") or existing.get("priority"),
                "processing_result": current_state.get(
                    "processing_result"
                ) or existing.get("processing_result"),
                "status": current_state.get("status") or "processing",
            }
            await db_tool.save_ticket(merged)

            # 当 human_review_wait 标记 __review_requested__，可以提前结束驱动
            if node_output.get("__review_requested__"):
                return current_state
    return current_state


async def _seed_pending_review(
    app: FastAPI,
    ticket_id: str,
    trigger_type: str,
    trigger_reason: str,
    ai_recommended: str | None = "approve",
    confidence: float = 0.8,
) -> str:
    """直接写入一条 pending human_reviews 记录（绕过 LLM 路径，模拟人工挂起）。"""
    from datetime import datetime

    from src.multi_agent_system.core.logging import generate_trace_id

    ai_suggestion = None
    if ai_recommended is not None:
        ai_suggestion = {
            "recommended_decision": ai_recommended,
            "confidence": confidence,
            "reasoning": "测试建议",
            "key_concerns": [],
        }
    review_id = f"HR-{generate_trace_id()}"
    await app.state.db_manager.create_pending_review({
        "review_id": review_id,
        "ticket_id": ticket_id,
        "trigger_type": trigger_type,
        "trigger_reason": trigger_reason,
        "ai_suggestion": ai_suggestion,
        "created_at": datetime.now().isoformat(),
    })
    return review_id


async def _seed_ticket(
    app: FastAPI,
    ticket_id: str,
    *,
    content: str = "测试工单内容",
    category: str = "complaint",
    priority: str = "P1",
    processing_result: str | None = "AI 已尝试处理",
    review_score: float | None = 0.4,
    retry_count: int = 0,
    status: str = "pending_human_review",
    trace_id: str | None = None,
) -> dict[str, Any]:
    """直接向 DB 写入一条工单记录，便于决策测试快速 setup。"""
    from datetime import datetime

    ticket = {
        "ticket_id": ticket_id,
        "content": content,
        "category": category,
        "priority": priority,
        "processing_result": processing_result,
        "review_score": review_score,
        "retry_count": retry_count,
        "status": status,
        "created_at": datetime.now().isoformat(),
    }
    if trace_id:
        ticket["trace_id"] = trace_id
    await app.state.db_manager.save_ticket(ticket)
    return ticket


# ============================================================
# 9.1 投诉工单 escalate → approve → complete
# ============================================================


class TestEscalateApproveFlow:
    """9.1 投诉工单 → escalate → human_review_wait → approve → notify → complete。"""

    @pytest.mark.asyncio
    async def test_escalate_approve_full_flow(self, e2e_app: FastAPI, client: TestClient):
        # 占位模式（无 Agent 注入），分类走关键词匹配：投诉 → escalate 路径
        # 注入 db_manager + mock coordinator 到工作流模块，供 human_review_wait 使用
        workflow = build_ticket_graph(
            agents={"coordinator": _make_mock_coordinator("approve", 0.85)},
            db_manager=e2e_app.state.db_manager,
        )
        e2e_app.state.workflow = workflow
        e2e_app.state.coordinator = _make_mock_coordinator("approve", 0.85)

        state = create_initial_state("我要投诉你们的客服服务态度极差！")
        ticket_id = state["ticket_id"]

        # 阶段 1：跑工作流，预期停在 pending_human_review
        await _run_workflow_until_pending(e2e_app, ticket_id, state)

        # 校验工单状态
        ticket = await e2e_app.state.db_tool.get_ticket(ticket_id)
        assert ticket["status"] == "pending_human_review"
        assert ticket["category"] == "complaint"

        # 队列中应能查到该工单
        queue_resp = client.get("/api/reviews/queue").json()
        assert any(q["ticket_id"] == ticket_id for q in queue_resp["queue"])

        # 阶段 2：人工决策 approve，恢复工作流
        result = await resume_from_human_decision(
            app=e2e_app,
            ticket_id=ticket_id,
            decision="approve",
            decision_reason="确认无问题",
            rewritten_result=None,
            reviewer_id="U-test",
        )

        assert result["workflow_resumed"] is True
        # approve 路由：notify -> complete，next_node 是最后执行的节点
        assert result["next_node"] in ("notify", "complete")

        # 阶段 3：最终工单应为 completed
        final_ticket = await e2e_app.state.db_tool.get_ticket(ticket_id)
        assert final_ticket["status"] == "completed"


# ============================================================
# 9.2 review_failed → rewrite → complete
# ============================================================


class TestReviewFailedRewriteFlow:
    """9.2 review 失败 3 次 → human_review_wait → 决策 rewrite → notify → complete。"""

    @pytest.mark.asyncio
    async def test_review_failed_rewrite_full_flow(self, e2e_app: FastAPI, client: TestClient):
        # 直接 seed 工单 + pending 审核单，跳过工作流前半段（已由 workflow 单测覆盖）
        ticket_id = "TK-e2e-review-failed"
        await _seed_ticket(
            e2e_app,
            ticket_id,
            content="系统报错崩溃无法登录",
            category="technical",
            priority="P1",
            processing_result="AI 自动回复结果不够完整",
            review_score=0.4,
            retry_count=3,
            status="pending_human_review",
        )
        await _seed_pending_review(
            e2e_app,
            ticket_id,
            trigger_type="review_failed",
            trigger_reason="AI 多次审核未通过（retry_count=3）",
            ai_recommended="rewrite",
            confidence=0.7,
        )

        # 校验队列筛选
        queue_resp = client.get(
            "/api/reviews/queue", params={"trigger_type": "review_failed"}
        ).json()
        assert any(q["ticket_id"] == ticket_id for q in queue_resp["queue"])

        # 调用决策接口（API 层）—— 验证全链路
        decision_resp = client.post(
            f"/api/reviews/{ticket_id}/decision",
            json={
                "decision": "rewrite",
                "decision_reason": "需要补全信息",
                "rewritten_result": "人工改写：详细解决方案如下...",
                "reviewer_id": "U-test",
            },
        )
        assert decision_resp.status_code == 200, decision_resp.text
        body = decision_resp.json()
        assert body["workflow_resumed"] is True

        # 最终工单 completed
        final = await e2e_app.state.db_tool.get_ticket(ticket_id)
        assert final["status"] == "completed"
        # 回归断言：ticket.processing_result 必须被 rewritten_result 覆盖
        # （修复前 bug：resume 循环内 existing 快照未刷新，notify/complete 节点不返回
        # processing_result 时被旧值回写覆盖）
        assert "人工改写" in (final.get("processing_result") or ""), (
            f"processing_result 应含人工改写内容，实际: {final.get('processing_result')!r}"
        )

        # human_reviews 记录已 decided，rewritten_result 已持久化
        reviews = await e2e_app.state.db_manager.list_reviews_by_ticket(ticket_id)
        assert len(reviews) == 1
        assert reviews[0]["status"] == "decided"
        assert reviews[0]["decision"] == "rewrite"
        assert "人工改写" in (reviews[0]["rewritten_result"] or "")


# ============================================================
# 9.3 review_failed → reprocess → process → review → 通过
# ============================================================


class TestReviewFailedReprocessFlow:
    """9.3 review 失败 3 次 → human_review_wait → 决策 reprocess → process → review → 通过。"""

    @pytest.mark.asyncio
    async def test_review_failed_reprocess_full_flow(self, e2e_app: FastAPI, client: TestClient):
        ticket_id = "TK-e2e-reprocess"
        await _seed_ticket(
            e2e_app,
            ticket_id,
            content="系统崩溃需要技术支持",
            category="technical",
            priority="P2",
            processing_result="旧的 AI 处理结果",
            review_score=0.4,
            retry_count=3,
            status="pending_human_review",
        )
        await _seed_pending_review(
            e2e_app,
            ticket_id,
            trigger_type="review_failed",
            trigger_reason="AI 多次审核未通过",
            ai_recommended="reprocess",
            confidence=0.6,
        )

        # 调用 reprocess 决策
        decision_resp = client.post(
            f"/api/reviews/{ticket_id}/decision",
            json={
                "decision": "reprocess",
                "decision_reason": "重新走流程",
                "reviewer_id": "U-test",
            },
        )
        assert decision_resp.status_code == 200
        # reprocess 路由：process → review → notify → complete
        assert decision_resp.json()["next_node"] in ("process", "complete")

        # reprocess 子图：process → review → notify → complete（占位模式下评分高通过）
        final = await e2e_app.state.db_tool.get_ticket(ticket_id)
        assert final["status"] == "completed"


# ============================================================
# 9.4 工作流异常 → error_fallback → approve → 恢复
# ============================================================


class TestErrorFallbackApproveFlow:
    """9.4 工作流异常 → error_fallback 触发 → 决策 approve → 恢复。"""

    @pytest.mark.asyncio
    async def test_error_fallback_approve_full_flow(self, e2e_app: FastAPI, client: TestClient):
        ticket_id = "TK-e2e-error-fallback"
        await _seed_ticket(
            e2e_app,
            ticket_id,
            content="模拟异常工单",
            category="technical",
            priority="P2",
            processing_result="AI 处理过程中产生异常",
            review_score=None,
            retry_count=0,
            status="pending_human_review",
            trace_id=None,
        )
        await _seed_pending_review(
            e2e_app,
            ticket_id,
            trigger_type="error_fallback",
            trigger_reason="工作流执行异常: RuntimeError(test)",
            ai_recommended="approve",
            confidence=0.6,
        )

        # 验证 trigger_type 筛选生效
        queue_resp = client.get(
            "/api/reviews/queue", params={"trigger_type": "error_fallback"}
        ).json()
        assert any(q["ticket_id"] == ticket_id for q in queue_resp["queue"])

        # approve 决策恢复
        decision_resp = client.post(
            f"/api/reviews/{ticket_id}/decision",
            json={
                "decision": "approve",
                "decision_reason": "确认 AI 结果可接受",
                "reviewer_id": "U-test",
            },
        )
        assert decision_resp.status_code == 200
        # approve 路由：notify → complete，next_node 是最后执行的节点
        assert decision_resp.json()["next_node"] in ("notify", "complete")

        final = await e2e_app.state.db_tool.get_ticket(ticket_id)
        assert final["status"] == "completed"


# ============================================================
# 9.5 completed + satisfied=false → user_request 审核
# ============================================================


class TestUserRequestFlow:
    """9.5 completed 工单 + satisfied=false → user_request 审核。"""

    @pytest.mark.asyncio
    async def test_feedback_unsatisfied_triggers_user_request_review(
        self, e2e_app: FastAPI, client: TestClient
    ):
        ticket_id = "TK-e2e-user-req"
        # seed 一条 completed 工单
        await _seed_ticket(
            e2e_app,
            ticket_id,
            content="账单问题已处理但用户不满意",
            category="billing",
            priority="P2",
            processing_result="AI 已处理",
            review_score=0.8,
            retry_count=0,
            status="completed",
        )

        # 注入 mock coordinator（feedback 接口会尝试调用）
        e2e_app.state.coordinator = _make_mock_coordinator("reprocess", 0.65)

        # 调用 feedback 接口，satisfied=false
        resp = client.post(
            f"/api/tickets/{ticket_id}/feedback",
            json={"satisfied": False},
        )
        assert resp.status_code == 200, resp.text

        # 工单状态应被改回 pending_human_review
        ticket = await e2e_app.state.db_tool.get_ticket(ticket_id)
        assert ticket["status"] == "pending_human_review"
        assert ticket["satisfied"] in (0, False)

        # human_reviews 应有一条 user_request 类型 pending 记录
        reviews = await e2e_app.state.db_manager.list_reviews_by_ticket(ticket_id)
        assert len(reviews) >= 1
        user_req = [r for r in reviews if r["trigger_type"] == "user_request"]
        assert len(user_req) == 1
        assert user_req[0]["status"] == "pending"

        # 队列中按 user_request 筛选应能查到
        queue_resp = client.get(
            "/api/reviews/queue", params={"trigger_type": "user_request"}
        ).json()
        assert any(q["ticket_id"] == ticket_id for q in queue_resp["queue"])


# ============================================================
# 9.6 trace 完整展示 AI → 暂停 → 人工 → 恢复
# ============================================================


class TestTraceIntegration:
    """9.6 trace 接口能完整展示"AI → 暂停 → 人工 → 恢复"全过程。

    使用占位模式工作流 + 无 TraceManager（验证在 TraceManager 缺失场景下的健壮性），
    同时手动构造一条 trace + human_decision span 模拟完整数据。
    """

    @pytest.mark.asyncio
    async def test_trace_returns_human_decision_spans(
        self, e2e_app: FastAPI, client: TestClient
    ):
        import time

        from src.multi_agent_system.core.logging import generate_trace_id

        ticket_id = "TK-e2e-trace"
        trace_id = f"tr-{generate_trace_id()}"
        await _seed_ticket(
            e2e_app,
            ticket_id,
            content="trace 集成测试",
            category="complaint",
            priority="P1",
            processing_result="AI 处理",
            status="completed",
            trace_id=trace_id,
        )

        # 手动插入 trace + 两种 span（start_time/end_time 为 Unix 时间戳）
        db = e2e_app.state.db_manager
        now_ts = time.time()
        await db.save_trace({
            "trace_id": trace_id,
            "ticket_id": ticket_id,
            "status": "completed",
            "start_time": now_ts,
            "end_time": now_ts + 320,
            "duration": 320,
            "total_tokens": 100,
            "total_tool_calls": 2,
            "node_count": 5,
        })
        # node span
        await db.save_span({
            "span_id": f"sp-{generate_trace_id()}",
            "trace_id": trace_id,
            "parent_span_id": None,
            "span_type": "node",
            "name": "process",
            "status": "ok",
            "start_time": now_ts,
            "end_time": now_ts + 100,
            "duration": 100,
            "input_data": json.dumps({"k": "v"}),
            "output_data": json.dumps({"status": "ok"}),
            "metadata": json.dumps({}),
        })
        # human_decision span
        human_span_id = f"sp-{generate_trace_id()}"
        await db.save_span({
            "span_id": human_span_id,
            "trace_id": trace_id,
            "parent_span_id": None,
            "span_type": "human_decision",
            "name": "apply_human_decision",
            "status": "decided",
            "start_time": now_ts + 100,
            "end_time": now_ts + 320,
            "duration": 220,
            "input_data": json.dumps({"review_id": "HR-1", "decision": "approve"}),
            "output_data": json.dumps({
                "status": "decided",
                "decision": "approve",
                "reviewer_id": "U-test",
                "ai_adopted": True,
            }),
            "metadata": json.dumps({"decision_reason": "ok"}),
        })

        # 调 trace 接口
        resp = client.get(f"/api/tickets/{ticket_id}/trace")
        assert resp.status_code == 200
        body = resp.json()
        assert body["trace_id"] == trace_id

        # 在 span 树中应能找到 human_decision 类型
        all_spans: list[dict] = []

        def _walk(spans: list[dict]) -> None:
            for s in spans:
                all_spans.append(s)
                _walk(s.get("children") or [])

        _walk(body["spans"])
        human_spans = [s for s in all_spans if s["span_type"] == "human_decision"]
        assert len(human_spans) >= 1
        assert human_spans[0]["output_data"]["decision"] == "approve"
        assert human_spans[0]["output_data"]["ai_adopted"] is True


# ============================================================
# 9.7 ai_adoption_rate 统计正确
# ============================================================


class TestAIAdoptionRateStats:
    """9.7 GET /api/reviews/stats 返回的 ai_adoption_rate 与人工决策/AI 建议匹配关系一致。"""

    @pytest.mark.asyncio
    async def test_ai_adoption_rate_calculation(self, e2e_app: FastAPI, client: TestClient):
        # 构造 4 条已决策记录：
        # - 2 条采纳 AI（decision == recommended）
        # - 1 条未采纳（decision != recommended）
        # - 1 条无 AI 建议（不计入分母）
        cases = [
            ("TK-adopt-1", "approve", "approve"),   # 采纳
            ("TK-adopt-2", "rewrite", "rewrite"),   # 采纳
            ("TK-reject-1", "approve", "reject"),   # 未采纳
            ("TK-no-ai-1", None, "approve"),        # 无 AI 建议，不计入
        ]
        from datetime import datetime

        for idx, (ticket_id, recommended, decision) in enumerate(cases):
            await _seed_ticket(
                e2e_app,
                ticket_id,
                content=f"stats {idx}",
                category="complaint",
                priority="P1",
                processing_result="结果",
                status="completed",
            )
            review_id = await _seed_pending_review(
                e2e_app,
                ticket_id,
                trigger_type="escalate",
                trigger_reason="stats 测试",
                ai_recommended=recommended,
            )
            # 标记为已决策
            await e2e_app.state.db_manager.update_review_decision(
                review_id,
                {
                    "decision": decision,
                    "decision_reason": "测试",
                    "reviewer_id": "U-test",
                    "status": "decided",
                    "decided_at": datetime.now().isoformat(),
                },
            )

        resp = client.get("/api/reviews/stats")
        assert resp.status_code == 200
        body = resp.json()

        # pending=0（全部已决策），decided_today=4
        assert body["pending_count"] == 0
        assert body["decided_today"] == 4
        # 分母 = 3（有 AI 建议的可比较记录），分子 = 2（采纳）→ 0.6667
        assert body["ai_adoption_rate"] == round(2 / 3, 4)
