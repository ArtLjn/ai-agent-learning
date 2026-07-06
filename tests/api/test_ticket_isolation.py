"""用户隔离测试：user 角色只能看自己的工单；admin/developer 看全部。

覆盖：
- POST /tickets：session user_id 自动注入（防伪造 body.user_id）
- GET /tickets：user 角色按 session.user_id 过滤；admin/developer 看全部
- GET /tickets/{id}：user 看他人 403；admin/developer 放行
- GET /tickets/{id}/messages：user 看他人 403
- POST /tickets/{id}/messages：user 给他人补充 403
- POST /tickets/{id}/feedback：user 给他人反馈 403
- 演示模式（auth_enabled=false）：全放行
"""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.auth_routes import router as auth_router
from src.multi_agent_system.api.routes import router as biz_router
from src.multi_agent_system.core.database import DatabaseManager
from tests.conftest import TEST_DATABASE_URL

_SESSION_SECRET = "test-ticket-isolation-secret-32-chars-or-more"


def _build_app() -> FastAPI:
    """构造测试 app，db_manager 在 lifespan 内创建（绑定 TestClient 的 loop）。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_manager = DatabaseManager(database_url=TEST_DATABASE_URL)
        await db_manager.initialize()
        await db_manager.truncate_all()
        app.state.db_manager = db_manager
        # db_tool 桥接到真 db_manager，让 list_tickets / save_ticket 走真 DB
        db_tool = MagicMock()
        db_tool.save_ticket = AsyncMock(side_effect=db_manager.save_ticket)
        db_tool.get_ticket = AsyncMock(side_effect=db_manager.get_ticket)
        db_tool.list_tickets = AsyncMock(side_effect=db_manager.list_tickets)
        app.state.db_tool = db_tool
        app.state.coordinator = None
        app.state.analytics_tool = MagicMock()
        app.state.knowledge_tool = None
        app.state.memory_manager = None
        app.state.tool_registry = None
        app.state.workflow = MagicMock()
        app.state.trace_manager = None
        app.state.ticket_intent_agent = None  # 走 fallback 路径，不调 LLM
        yield
        await db_manager.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=_SESSION_SECRET)
    app.include_router(auth_router, prefix="/api")
    app.include_router(biz_router, prefix="/api")
    return app


@pytest.fixture
def app():
    return _build_app()


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        app.state._portal = c.portal
        yield c


# ============================================================
# 工具函数
# ============================================================


def _register(client: TestClient, username: str) -> dict[str, Any]:
    """注册用户并自动登录（session 写入）。"""
    resp = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "secret123",
            "nickname": username.title(),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["user"]


def _login(client: TestClient, username: str) -> None:
    """重新登录指定用户。"""
    client.post("/api/auth/logout")
    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": "secret123"},
    )
    assert resp.status_code == 200, f"登录失败: {resp.text}"


def _promote(client: TestClient, app: FastAPI, user_id: str, role: str) -> None:
    """DB 层面改 role，然后重新登录让 session 拿到新 role。"""
    updated = client.portal.call(app.state.db_manager.update_user_role, user_id, role)
    assert updated is not None and updated["role"] == role
    _login(client, updated["username"])


def _seed_ticket(app: FastAPI, ticket_id: str, user_id: str) -> dict[str, Any]:
    """直接通过 db_manager 植入一条工单。"""
    ticket = {
        "ticket_id": ticket_id,
        "content": f"工单 {ticket_id} 内容",
        "user_id": user_id,
        "category": "inquiry",
        "priority": "P2",
        "status": "completed",
        "review_score": None,
        "retry_count": 0,
        "references": [],
        "created_at": "2026-07-05T10:00:00",
    }
    client_portal = app.state._portal
    client_portal.call(app.state.db_manager.save_ticket, ticket)
    return ticket


# ============================================================
# 测试用例
# ============================================================


class TestCreateTicketUserIdInjection:
    """POST /tickets 的 user_id 自动注入。"""

    def test_create_injects_session_user_id(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """登录用户创建工单，body 里的 user_id（伪造）被忽略，写入的是 session user_id。"""
        user = _register(client, "alice")
        # 前端传伪造 user_id="FAKE"
        resp = client.post(
            "/api/tickets",
            json={"content": "测试工单内容长度至少八个字符", "user_id": "FAKE"},
        )
        assert resp.status_code == 200, resp.text
        ticket_id = resp.json()["ticket_id"]

        # DB 里存的应该是 session user_id，不是 "FAKE"
        ticket = client.portal.call(app.state.db_manager.get_ticket, ticket_id)
        assert ticket["user_id"] == user["user_id"]
        assert ticket["user_id"] != "FAKE"


class TestListTicketsIsolation:
    """GET /tickets 列表接口的 user 隔离。"""

    def test_user_sees_only_own_tickets(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """user 角色：列表只返回自己的工单。"""
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        # 当前 session 是 bob（注册 bob 后自动登录）
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-alice-1", alice["user_id"])
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _login(client, "alice")

        resp = client.get("/api/tickets")
        assert resp.status_code == 200
        items = resp.json()
        ticket_ids = [t["ticket_id"] for t in items]
        assert "TK-alice-1" in ticket_ids
        assert "TK-bob-1" not in ticket_ids

    def test_admin_sees_all_tickets(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """admin 角色：列表返回所有用户的工单。"""
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-alice-1", alice["user_id"])
        _seed_ticket(app, "TK-bob-1", bob["user_id"])

        # 升 alice 为 admin 并重新登录
        _promote(client, app, alice["user_id"], "admin")

        resp = client.get("/api/tickets")
        assert resp.status_code == 200
        items = resp.json()
        ticket_ids = [t["ticket_id"] for t in items]
        assert "TK-alice-1" in ticket_ids
        assert "TK-bob-1" in ticket_ids

    def test_developer_sees_all_tickets(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """developer 角色：列表返回所有工单（调试需要）。"""
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-alice-1", alice["user_id"])
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _promote(client, app, alice["user_id"], "developer")

        resp = client.get("/api/tickets")
        assert resp.status_code == 200
        items = resp.json()
        ticket_ids = [t["ticket_id"] for t in items]
        assert "TK-alice-1" in ticket_ids
        assert "TK-bob-1" in ticket_ids


class TestTicketDetailIsolation:
    """GET /tickets/{id} 详情接口的 user 隔离。"""

    def test_user_views_own_ticket_200(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _seed_ticket(app, "TK-alice-1", alice["user_id"])

        resp = client.get("/api/tickets/TK-alice-1")
        assert resp.status_code == 200

    def test_user_views_other_user_ticket_403(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _login(client, "alice")

        resp = client.get("/api/tickets/TK-bob-1")
        assert resp.status_code == 403

    def test_admin_views_other_user_ticket_200(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _promote(client, app, alice["user_id"], "admin")

        resp = client.get("/api/tickets/TK-bob-1")
        assert resp.status_code == 200

    def test_developer_views_other_user_ticket_200(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _promote(client, app, alice["user_id"], "developer")

        resp = client.get("/api/tickets/TK-bob-1")
        assert resp.status_code == 200


class TestMessagesAndFeedbackIsolation:
    """消息列表 / 补充 / 反馈接口的 user 隔离。"""

    def test_user_lists_other_user_messages_403(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _login(client, "alice")

        resp = client.get("/api/tickets/TK-bob-1/messages")
        assert resp.status_code == 403

    def test_user_posts_message_to_other_user_ticket_403(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        # 工单处于 waiting_user_input 状态才有补充意义，但 403 应该在状态校验之前
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _login(client, "alice")

        resp = client.post(
            "/api/tickets/TK-bob-1/messages",
            json={"content": "补充信息"},
        )
        assert resp.status_code == 403

    def test_user_submits_feedback_for_other_user_ticket_403(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _login(client, "alice")

        resp = client.post(
            "/api/tickets/TK-bob-1/feedback",
            json={"satisfied": False},
        )
        assert resp.status_code == 403

    def test_admin_cannot_submit_feedback_for_user_ticket(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """admin 可查看工单，但不能代替用户提交满意度反馈。"""
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _promote(client, app, alice["user_id"], "admin")

        resp = client.post(
            "/api/tickets/TK-bob-1/feedback",
            json={"satisfied": False},
        )
        assert resp.status_code == 403

    def test_developer_cannot_submit_feedback_for_user_ticket(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """developer 可调试工单，但不能代替用户提交满意度反馈。"""
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _promote(client, app, alice["user_id"], "developer")

        resp = client.post(
            "/api/tickets/TK-bob-1/feedback",
            json={"satisfied": False},
        )
        assert resp.status_code == 403


class TestDemoModeBypass:
    """演示模式（auth_enabled=false）下全放行。"""

    @pytest.fixture(autouse=True)
    def _disable_auth(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "false")

    def test_demo_mode_user_views_any_ticket_200(
        self, client: TestClient, app: FastAPI
    ) -> None:
        # 演示模式不要求登录；直接植入工单访问
        _seed_ticket(app, "TK-demo-1", "any-user-id")
        resp = client.get("/api/tickets/TK-demo-1")
        assert resp.status_code == 200


# ============================================================
# 辅助：注销当前用户 + 注册新用户（不保持原 session）
# ============================================================


def _logout_then_register_other(client: TestClient, username: str) -> None:
    """注销当前用户，再注册新用户（会自动登录新用户）。"""
    client.post("/api/auth/logout")
    _register(client, username)
