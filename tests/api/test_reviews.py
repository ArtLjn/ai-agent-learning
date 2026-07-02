"""人工审核工作台 API 端点测试。"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.routes import router
from src.multi_agent_system.core.database import DatabaseManager
from src.multi_agent_system.core.trace import TraceManager
from tests.conftest import TEST_DATABASE_URL


def _run(client: TestClient, fn, *args, **kwargs):
    """在 TestClient 的 portal 内同步调用 async 函数（避免跨 event loop）。"""
    return client.portal.call(fn, *args, **kwargs)


def _build_app() -> FastAPI:
    """构建测试用 FastAPI 应用，db_manager 在 lifespan 内创建（绑定 TestClient 的 loop）。"""
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_manager = DatabaseManager(database_url=TEST_DATABASE_URL)
        await db_manager.initialize()
        await db_manager.truncate_all()
        app.state.db_manager = db_manager
        app.state.db_tool = MagicMock()
        app.state.db_tool.save_ticket = AsyncMock()
        app.state.db_tool.get_ticket = AsyncMock(return_value=None)
        app.state.coordinator = None
        app.state.analytics_tool = MagicMock()
        app.state.knowledge_tool = None
        app.state.memory_manager = None
        app.state.tool_registry = None
        app.state.workflow = MagicMock()
        app.state.trace_manager = None
        yield
        await db_manager.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret-32-chars-or-more")
    app.include_router(router, prefix="/api")
    return app


@pytest.fixture
def app():
    return _build_app()


@pytest.fixture(autouse=True)
def _disable_auth_for_reviews_tests(monkeypatch):
    """reviews/* 路由现在带 require_role("admin")，测试 fixture
    无登录态会被 401/403 拦下。演示模式下 require_role 视为 admin 放行，
    让既有测试逻辑保持不变。"""
    monkeypatch.setenv("AUTH_ENABLED", "false")


@pytest.fixture
def client(app) -> TestClient:
    """用 with 触发 lifespan 让 db_manager 在 portal 的 loop 内创建。"""
    with TestClient(app) as c:
        app.state._portal = c.portal
        yield c


def _seed_ticket(app: FastAPI, ticket_id: str, **overrides) -> dict:
    """写入一条工单记录到测试 DB（在 TestClient 的 portal loop 内执行）。"""
    ticket = {
        "ticket_id": ticket_id,
        "content": "我对昨天购买的商品非常不满意，要求退款" * 5,
        "category": "complaint",
        "priority": "P1",
        "processing_result": "已尝试自动回复",
        "review_score": 0.4,
        "retry_count": 3,
        "status": "pending_human_review",
        "references": [],
        "created_at": "2026-06-27T10:00:00",
    }
    ticket.update(overrides)
    app.state._portal.call(app.state.db_manager.save_ticket, ticket)
    # 让 db_tool.get_ticket 也返回同一份数据
    app.state.db_tool.get_ticket = AsyncMock(return_value=ticket)
    return ticket


def _seed_review(app: FastAPI, review_id: str, ticket_id: str, **overrides) -> dict:
    """写入一条 human_reviews 记录。

    若提供 status='decided'，则额外调用 update_review_decision 标记为已决策。
    """
    review = {
        "review_id": review_id,
        "ticket_id": ticket_id,
        "trigger_type": "escalate",
        "trigger_reason": "投诉类工单",
        "ai_suggestion": {
            "recommended_decision": "reprocess",
            "confidence": 0.7,
            "reasoning": "retry 次数过多",
            "key_concerns": ["投诉"],
        },
        "created_at": "2026-06-27T10:00:00",
        "status": "pending",
    }
    review.update(overrides)
    final_status = review.pop("status")
    decision = review.pop("decision", None)
    reviewer_id = review.pop("reviewer_id", None)
    decided_at = review.pop("decided_at", None)
    app.state._portal.call(app.state.db_manager.create_pending_review, review)
    if final_status == "decided":
        app.state._portal.call(
            app.state.db_manager.update_review_decision,
            review_id,
            {
                "status": "decided",
                "decision": decision,
                "reviewer_id": reviewer_id,
                "decided_at": decided_at or "2026-06-27T11:00:00",
            },
        )
    return review


# ============================================================
# 队列查询
# ============================================================


def test_list_review_queue_empty(client: TestClient) -> None:
    """空队列返回空列表。"""
    resp = client.get("/api/reviews/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["queue"] == []
    assert body["total"] == 0


def test_request_info_decision_pauses_for_user_input(
    client: TestClient,
    app: FastAPI,
) -> None:
    """审核员可请求用户补充信息，工单进入待用户补充状态。"""
    _seed_ticket(app, "TK-info-1", content="退款还没有到账")
    _seed_review(
        app,
        "HR-info-1",
        "TK-info-1",
        trigger_type="review_failed",
        trigger_reason="缺少订单号",
    )

    resp = client.post(
        "/api/reviews/TK-info-1/decision",
        json={
            "decision": "request_info",
            "decision_reason": "请补充订单号",
            "reviewer_id": "reviewer-001",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["next_node"] == "waiting_user_input"
    assert body["workflow_resumed"] is False

    ticket = _run(client, app.state.db_manager.get_ticket, "TK-info-1")
    assert ticket["status"] == "waiting_user_input"

    reviews = _run(client, app.state.db_manager.list_reviews_by_ticket, "TK-info-1")
    assert reviews[-1]["decision"] == "request_info"
    assert reviews[-1]["status"] == "decided"

    messages = _run(client, app.state.db_manager.list_ticket_messages, "TK-info-1")
    assert messages[-1]["sender_type"] == "reviewer"
    assert messages[-1]["content"] == "请补充订单号"


def test_request_info_records_trace_span(
    client: TestClient,
    app: FastAPI,
) -> None:
    """人工请求补充信息应进入原 trace。"""
    _seed_ticket(app, "TK-info-trace", content="退款还没有到账")
    _seed_review(
        app,
        "HR-info-trace",
        "TK-info-trace",
        trigger_type="review_failed",
        trigger_reason="缺少订单号",
    )
    app.state.trace_manager = TraceManager(app.state.db_manager)
    trace_id = _run(client, app.state.trace_manager.start_trace, "TK-info-trace")
    _run(client, app.state.trace_manager.finish_trace, trace_id, "completed")

    resp = client.post(
        "/api/reviews/TK-info-trace/decision",
        json={
            "decision": "request_info",
            "decision_reason": "请补充订单号",
            "reviewer_id": "reviewer-001",
        },
    )

    assert resp.status_code == 200
    spans = _run(client, app.state.db_manager.get_spans_by_trace, trace_id)
    names = [span["name"] for span in spans]
    assert "user_input_requested" in names


def test_user_message_requires_waiting_state(
    client: TestClient,
    app: FastAPI,
) -> None:
    """已完成工单不允许追加补充消息。"""
    _seed_ticket(app, "TK-msg-state", status="completed")

    resp = client.post(
        "/api/tickets/TK-msg-state/messages",
        json={"content": "订单号是 123456", "sender_id": "user-001"},
    )

    assert resp.status_code == 409


def test_user_message_resumes_workflow(
    client: TestClient,
    app: FastAPI,
) -> None:
    """用户在待补充状态追加消息后触发恢复处理。"""
    _seed_ticket(app, "TK-user-1", content="退款没有到账", status="waiting_user_input")

    with patch(
        "src.multi_agent_system.api.routes.resume_from_user_input",
        new_callable=AsyncMock,
    ) as mock_resume:
        mock_resume.return_value = {
            "status": "ok",
            "ticket_id": "TK-user-1",
            "workflow_resumed": True,
            "next_node": "process",
        }
        resp = client.post(
            "/api/tickets/TK-user-1/messages",
            json={"content": "订单号是 123456", "sender_id": "user-001"},
        )

    assert resp.status_code == 200
    assert resp.json()["workflow_resumed"] is True
    mock_resume.assert_awaited_once()

    messages = _run(client, app.state.db_manager.list_ticket_messages, "TK-user-1")
    assert messages[-1]["sender_type"] == "user"
    assert messages[-1]["content"] == "订单号是 123456"


def test_user_message_records_trace_span(
    client: TestClient,
    app: FastAPI,
) -> None:
    """用户补充消息应作为业务事件写入原 trace。"""
    _seed_ticket(
        app,
        "TK-user-trace-api",
        content="退款没有到账",
        status="waiting_user_input",
    )
    app.state.trace_manager = TraceManager(app.state.db_manager)
    trace_id = _run(client, app.state.trace_manager.start_trace, "TK-user-trace-api")
    _run(client, app.state.trace_manager.finish_trace, trace_id, "completed")

    with patch(
        "src.multi_agent_system.api.routes.resume_from_user_input",
        new_callable=AsyncMock,
    ) as mock_resume:
        mock_resume.return_value = {
            "status": "ok",
            "ticket_id": "TK-user-trace-api",
            "workflow_resumed": True,
            "next_node": "process",
        }
        resp = client.post(
            "/api/tickets/TK-user-trace-api/messages",
            json={"content": "订单号是 123456", "sender_id": "user-001"},
        )

    assert resp.status_code == 200
    mock_resume.assert_awaited_once()
    spans = _run(client, app.state.db_manager.get_spans_by_trace, trace_id)
    names = [span["name"] for span in spans]
    assert "ticket_message_created" in names


def test_list_review_queue_returns_pending(app: FastAPI, client: TestClient) -> None:
    """队列查询返回 pending 审核单与工单快照。"""
    _seed_ticket(app, "TK-1")
    _seed_review(app, "HR-1", "TK-1")

    resp = client.get("/api/reviews/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["queue"][0]
    assert item["ticket_id"] == "TK-1"
    assert item["review_id"] == "HR-1"
    assert item["trigger_type"] == "escalate"
    assert item["category"] == "complaint"
    assert item["priority"] == "P1"
    assert len(item["content_preview"]) <= 100
    assert len(item["content_preview"]) > 0
    assert item["ai_suggestion"]["recommended_decision"] == "reprocess"
    assert item["waiting_seconds"] >= 0


def test_list_review_queue_filter_by_trigger(app: FastAPI, client: TestClient) -> None:
    """按 trigger_type 过滤。"""
    _seed_ticket(app, "TK-A")
    _seed_review(app, "HR-A1", "TK-A", trigger_type="escalate")
    _seed_ticket(app, "TK-B")
    _seed_review(app, "HR-B1", "TK-B", trigger_type="review_failed")

    resp = client.get("/api/reviews/queue?trigger_type=escalate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["queue"][0]["ticket_id"] == "TK-A"


def test_list_review_queue_priority_order(app: FastAPI, client: TestClient) -> None:
    """P0 工单排在 P3 前面。"""
    _seed_ticket(app, "TK-LOW", priority="P3")
    _seed_review(app, "HR-LOW", "TK-LOW")
    _seed_ticket(app, "TK-HIGH", priority="P0")
    _seed_review(app, "HR-HIGH", "TK-HIGH")

    resp = client.get("/api/reviews/queue")
    body = resp.json()
    priorities = [item["priority"] for item in body["queue"]]
    assert priorities.index("P0") < priorities.index("P3")


# ============================================================
# 审核详情
# ============================================================


def test_get_review_detail_not_found(client: TestClient) -> None:
    """工单不存在返回 404。"""
    resp = client.get("/api/reviews/TK-404")
    assert resp.status_code == 404


def test_get_review_detail_returns_context(app: FastAPI, client: TestClient) -> None:
    """详情接口返回完整审核上下文。"""
    _seed_ticket(app, "TK-D1")
    _seed_review(app, "HR-D1", "TK-D1")

    resp = client.get("/api/reviews/TK-D1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticket_id"] == "TK-D1"
    assert body["status"] == "pending_human_review"
    assert body["current_review"]["review_id"] == "HR-D1"
    assert body["current_review"]["ai_suggestion"]["recommended_decision"] == "reprocess"
    assert body["history_reviews"] == []


# ============================================================
# 提交决策
# ============================================================


def test_submit_decision_ticket_not_found(client: TestClient) -> None:
    """工单不存在 -> 404。"""
    resp = client.post(
        "/api/reviews/TK-404/decision",
        json={
            "decision": "approve",
            "decision_reason": "OK",
            "reviewer_id": "r1",
        },
    )
    assert resp.status_code == 404


def test_submit_decision_not_pending(app: FastAPI, client: TestClient) -> None:
    """工单不在 pending_human_review -> 409。"""
    _seed_ticket(app, "TK-D2", status="completed")
    resp = client.post(
        "/api/reviews/TK-D2/decision",
        json={
            "decision": "approve",
            "decision_reason": "OK",
            "reviewer_id": "r1",
        },
    )
    assert resp.status_code == 409


def test_submit_decision_rewrite_requires_result(app: FastAPI, client: TestClient) -> None:
    """rewrite 决策缺 rewritten_result -> 422（Pydantic model_validator 校验失败）。"""
    _seed_ticket(app, "TK-D3")
    resp = client.post(
        "/api/reviews/TK-D3/decision",
        json={
            "decision": "rewrite",
            "decision_reason": "需要重写",
            "reviewer_id": "r1",
        },
    )
    assert resp.status_code == 422
    # Pydantic v2 结构化错误：detail 是 list[dict]，错误消息嵌在 msg 字段
    detail_text = str(resp.json()["detail"])
    assert "REWRITE_RESULT_REQUIRED" in detail_text


def test_submit_decision_empty_reason(
    app: FastAPI, client: TestClient
) -> None:
    """空 decision_reason -> 422（Pydantic model_validator 校验失败）。"""
    _seed_ticket(app, "TK-D4")
    resp = client.post(
        "/api/reviews/TK-D4/decision",
        json={
            "decision": "approve",
            "decision_reason": "   ",
            "reviewer_id": "r1",
        },
    )
    assert resp.status_code == 422
    detail_text = str(resp.json()["detail"])
    assert "DECISION_REASON_REQUIRED" in detail_text


def test_submit_decision_approve_success(app: FastAPI, client: TestClient) -> None:
    """approve 决策成功调用 resume_from_human_decision。"""
    _seed_ticket(app, "TK-OK")
    _seed_review(app, "HR-OK", "TK-OK")

    with patch(
        "src.multi_agent_system.workflow.graph.resume_from_human_decision",
        new_callable=AsyncMock,
        return_value={"next_node": "notify", "workflow_resumed": True, "status": "ok"},
    ) as mock_resume:
        # resume_from_human_decision 是 workflow 模块的函数，但 API 内部 import 自
        # src.multi_agent_system.workflow.graph，patch 该模块即可
        resp = client.post(
            "/api/reviews/TK-OK/decision",
            json={
                "decision": "approve",
                "decision_reason": "同意",
                "reviewer_id": "reviewer-1",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_resumed"] is True
    assert body["next_node"] == "notify"
    assert mock_resume.await_count == 1
    call_kwargs = mock_resume.call_args.kwargs
    assert call_kwargs["decision"] == "approve"
    assert call_kwargs["reviewer_id"] == "reviewer-1"


def test_submit_decision_reject_success(app: FastAPI, client: TestClient) -> None:
    """reject 决策走通。"""
    _seed_ticket(app, "TK-RJ")
    _seed_review(app, "HR-RJ", "TK-RJ")

    with patch(
        "src.multi_agent_system.workflow.graph.resume_from_human_decision",
        new_callable=AsyncMock,
        return_value={"next_node": "complete", "workflow_resumed": True},
    ):
        resp = client.post(
            "/api/reviews/TK-RJ/decision",
            json={
                "decision": "reject",
                "decision_reason": "拒绝",
                "reviewer_id": "r1",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["next_node"] == "complete"


def test_submit_decision_rewrite_with_result(app: FastAPI, client: TestClient) -> None:
    """rewrite + rewritten_result 走通。"""
    _seed_ticket(app, "TK-RW")
    _seed_review(app, "HR-RW", "TK-RW")

    with patch(
        "src.multi_agent_system.workflow.graph.resume_from_human_decision",
        new_callable=AsyncMock,
        return_value={"next_node": "notify", "workflow_resumed": True},
    ) as mock_resume:
        resp = client.post(
            "/api/reviews/TK-RW/decision",
            json={
                "decision": "rewrite",
                "decision_reason": "改写",
                "rewritten_result": "新结果内容",
                "reviewer_id": "r1",
            },
        )
    assert resp.status_code == 200
    assert mock_resume.call_args.kwargs["rewritten_result"] == "新结果内容"


def test_submit_decision_idempotent(app: FastAPI, client: TestClient) -> None:
    """审核单已 decided -> 409。"""
    _seed_ticket(app, "TK-IDM")
    _seed_review(
        app, "HR-IDM", "TK-IDM", status="decided",
        decision="approve", reviewer_id="r1", decided_at="2026-06-27T11:00:00",
    )

    resp = client.post(
        "/api/reviews/TK-IDM/decision",
        json={
            "decision": "approve",
            "decision_reason": "再提交",
            "reviewer_id": "r2",
        },
    )
    assert resp.status_code == 409


def test_submit_decision_invalid_decision_value(app: FastAPI, client: TestClient) -> None:
    """非法 decision 值 -> 422（Pydantic 校验）。"""
    _seed_ticket(app, "TK-INV")
    resp = client.post(
        "/api/reviews/TK-INV/decision",
        json={
            "decision": "bogus",
            "decision_reason": "x",
            "reviewer_id": "r1",
        },
    )
    assert resp.status_code == 422


# ============================================================
# 统计
# ============================================================


def test_review_stats_empty(client: TestClient) -> None:
    """空库统计全为 0。"""
    resp = client.get("/api/reviews/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending_count"] == 0
    assert body["decided_today"] == 0
    assert body["decision_distribution"] == {}
    assert body["avg_decision_seconds"] == 0
    assert body["ai_adoption_rate"] == 0.0


def test_review_stats_with_data(app: FastAPI, client: TestClient) -> None:
    """统计含 pending + decided 数据。"""
    _seed_ticket(app, "TK-S1")
    _seed_review(app, "HR-S1", "TK-S1")  # pending

    _seed_ticket(app, "TK-S2", status="completed")
    _seed_review(
        app, "HR-S2", "TK-S2", status="decided",
        decision="reprocess", reviewer_id="r1",
        decided_at="2026-06-27T11:00:00",
    )

    resp = client.get("/api/reviews/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending_count"] == 1
    # decided_today: decided_at = 2026-06-27T11:00:00（仅当测试运行日期为同日时才计入）
    # 这里至少 decided_distribution 应包含 reprocess
    assert body["decision_distribution"].get("reprocess") == 1
    # ai_adoption_rate: HR-S2 decision=reprocess, recommended=reprocess -> adopted
    assert body["ai_adoption_rate"] == 1.0


# ============================================================
# Feedback 端点改造
# ============================================================


def test_feedback_satisfied_does_not_create_review(
    app: FastAPI, client: TestClient
) -> None:
    """satisfied=true 不创建审核单。"""
    _seed_ticket(app, "TK-FB1", status="completed")
    with patch(
        "src.multi_agent_system.core.evaluation.EvaluationCollector.record_user_feedback",
        new_callable=AsyncMock,
    ):
        resp = client.post(
            "/api/tickets/TK-FB1/feedback",
            json={"satisfied": True},
        )
    assert resp.status_code == 200
    pending = app.state._portal.call(
        app.state.db_manager.get_pending_review_by_ticket, "TK-FB1"
    )
    assert pending is None


def test_feedback_dissatisfied_creates_user_request_review(
    app: FastAPI, client: TestClient
) -> None:
    """satisfied=false + completed 工单 -> 创建 user_request pending 审核单。"""
    _seed_ticket(app, "TK-FB2", status="completed")
    with patch(
        "src.multi_agent_system.core.evaluation.EvaluationCollector.record_user_feedback",
        new_callable=AsyncMock,
    ):
        resp = client.post(
            "/api/tickets/TK-FB2/feedback",
            json={"satisfied": False},
        )
    assert resp.status_code == 200
    pending = app.state._portal.call(
        app.state.db_manager.get_pending_review_by_ticket, "TK-FB2"
    )
    assert pending is not None
    assert pending["trigger_type"] == "user_request"
    # 工单状态应被更新为 pending_human_review
    saved = app.state.db_tool.save_ticket.await_args_list[-1].args[0]
    assert saved["status"] == "pending_human_review"
