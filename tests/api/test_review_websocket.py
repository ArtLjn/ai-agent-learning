"""人工审核 WebSocket 事件推送测试。

覆盖两种事件：
- review_requested: 工单转入人工审核时广播
- review_decided:   审核员提交决策后广播
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.routes import (
    _broadcast_review_event,
    _global_ws_connections,
    router,
)
from src.multi_agent_system.core.auth import require_login
from src.multi_agent_system.core.database import DatabaseManager
from tests.conftest import TEST_DATABASE_URL


def _build_app() -> FastAPI:
    """构建测试用 FastAPI 应用，db_manager 在 lifespan 内创建。"""
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_manager = DatabaseManager(database_url=TEST_DATABASE_URL)
        await db_manager.initialize()
        await db_manager.truncate_all()
        app.state.db_manager = db_manager
        db_tool = MagicMock()
        db_tool.save_ticket = AsyncMock()
        db_tool.get_ticket = AsyncMock(return_value=None)
        app.state.db_tool = db_tool
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


def _build_auth_required_app() -> FastAPI:
    """构建带全局登录依赖的应用，复现生产路由注册方式。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret-32-chars-or-more")
    app.include_router(router, prefix="/api", dependencies=[Depends(require_login)])
    return app


@pytest.fixture
def app():
    return _build_app()


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        app.state._portal = c.portal
        yield c


@pytest.fixture(autouse=True)
def _disable_auth_for_ws_tests(monkeypatch):
    """reviews 路由带 require_role，演示模式下视为 admin 放行。"""
    monkeypatch.setenv("AUTH_ENABLED", "false")


@pytest.fixture(autouse=True)
def _clear_ws_connections():
    """每个用例前后清理全局 WS 连接池，避免跨用例污染。"""
    _global_ws_connections.clear()
    yield
    _global_ws_connections.clear()


def _seed_ticket(app: FastAPI, ticket_id: str, **overrides) -> dict:
    """写入一条工单记录到测试 DB。"""
    ticket = {
        "ticket_id": ticket_id,
        "content": "测试工单内容",
        "category": "complaint",
        "priority": "P1",
        "processing_result": "处理结果",
        "review_score": 0.4,
        "retry_count": 3,
        "status": "pending_human_review",
        "references": [],
        "created_at": "2026-06-27T10:00:00",
    }
    ticket.update(overrides)
    app.state._portal.call(app.state.db_manager.save_ticket, ticket)
    app.state.db_tool.get_ticket = AsyncMock(return_value=ticket)
    return ticket


def _seed_review(app: FastAPI, review_id: str, ticket_id: str) -> dict:
    """写入一条 pending human_reviews 记录。"""
    app.state._portal.call(app.state.db_manager.create_pending_review, {
        "review_id": review_id,
        "ticket_id": ticket_id,
        "trigger_type": "escalate",
        "trigger_reason": "投诉类工单",
        "ai_suggestion": None,
        "created_at": "2026-06-27T10:00:00",
    })


# ============================================================
# review_requested 广播
# ============================================================


def test_review_requested_broadcast_payload(app: FastAPI, client: TestClient) -> None:
    """feedback satisfied=false 触发 user_request 审核 -> WS 收到 review_requested。"""
    _seed_ticket(app, "TK-WS1", status="completed")

    with patch(
        "src.multi_agent_system.core.evaluation.EvaluationCollector.record_user_feedback",
        new_callable=AsyncMock,
    ):
        with client.websocket_connect("/api/ws/monitor") as ws:
            resp = client.post(
                "/api/tickets/TK-WS1/feedback",
                json={"satisfied": False, "reason": "处理结果没有解决员工服务请求"},
            )
            assert resp.status_code == 200
            msg = ws.receive_json()

    assert msg["type"] == "review_requested"
    assert msg["ticket_id"] == "TK-WS1"
    assert msg["trigger_type"] == "user_request"
    assert "priority" in msg
    assert "review_id" in msg
    assert "timestamp" in msg


def test_monitor_websocket_allows_global_login_dependency() -> None:
    """WebSocket 路由挂全局登录依赖时不应因缺少 Request 参数崩溃。"""
    app = _build_auth_required_app()

    with patch("src.multi_agent_system.core.auth.Settings") as settings_cls:
        settings_cls.return_value.auth_enabled = False
        with TestClient(app) as client:
            with client.websocket_connect("/api/ws/monitor") as ws:
                assert ws is not None


# ============================================================
# review_decided 广播
# ============================================================


def test_review_decided_broadcast_payload(app: FastAPI, client: TestClient) -> None:
    """提交决策成功 -> WS 收到 review_decided。"""
    _seed_ticket(app, "TK-WS2")
    _seed_review(app, "HR-WS2", "TK-WS2")

    with patch(
        "src.multi_agent_system.workflow.graph.resume_from_human_decision",
        new_callable=AsyncMock,
        return_value={"next_node": "notify", "workflow_resumed": True},
    ):
        with client.websocket_connect("/api/ws/monitor") as ws:
            resp = client.post(
                "/api/reviews/TK-WS2/decision",
                json={
                    "decision": "approve",
                    "decision_reason": "通过",
                    "reviewer_id": "reviewer-1",
                },
            )
            assert resp.status_code == 200
            msg = ws.receive_json()

    assert msg["type"] == "review_decided"
    assert msg["ticket_id"] == "TK-WS2"
    assert msg["decision"] == "approve"
    assert msg["reviewer_id"] == "reviewer-1"
    assert msg["next_node"] == "notify"
    assert "timestamp" in msg


# ============================================================
# _broadcast_review_event 单元测试
# ============================================================


class _FakeWebSocket:
    """模拟 WebSocket，记录发送过的消息。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._closed = False

    async def send_json(self, data: dict) -> None:
        if self._closed:
            raise RuntimeError("connection closed")
        self.sent.append(data)


@pytest.mark.asyncio
async def test_broadcast_review_event_sends_to_all() -> None:
    """_broadcast_review_event 向所有全局连接发送消息。"""
    ws1 = _FakeWebSocket()
    ws2 = _FakeWebSocket()
    _global_ws_connections.extend([ws1, ws2])

    await _broadcast_review_event(
        "review_requested",
        "TK-UNIT",
        {"trigger_type": "escalate", "priority": "P0"},
    )

    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 1
    msg = ws1.sent[0]
    assert msg["type"] == "review_requested"
    assert msg["ticket_id"] == "TK-UNIT"
    assert msg["trigger_type"] == "escalate"
    assert msg["priority"] == "P0"


@pytest.mark.asyncio
async def test_broadcast_review_event_removes_dead_connection() -> None:
    """发送失败的连接被清理。"""
    dead_ws = _FakeWebSocket()
    dead_ws._closed = True
    live_ws = _FakeWebSocket()
    _global_ws_connections.extend([dead_ws, live_ws])

    await _broadcast_review_event("review_decided", "TK-X", {"decision": "reject"})

    assert dead_ws not in _global_ws_connections
    assert live_ws in _global_ws_connections
    assert len(live_ws.sent) == 1


@pytest.mark.asyncio
async def test_broadcast_review_event_empty_payload() -> None:
    """payload=None 时只发送基础字段。"""
    ws = _FakeWebSocket()
    _global_ws_connections.append(ws)

    await _broadcast_review_event("review_decided", "TK-EMPTY", None)

    msg = ws.sent[0]
    assert msg["type"] == "review_decided"
    assert msg["ticket_id"] == "TK-EMPTY"
    assert "timestamp" in msg
