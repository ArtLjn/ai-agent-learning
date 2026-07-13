"""角色权限测试：require_role 依赖 + 路由 403/200。"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.auth_routes import router as auth_router
from src.multi_agent_system.api.user_routes import router as user_router
from src.multi_agent_system.api.routes import router as biz_router
from src.multi_agent_system.core.auth import hash_password, require_login
from src.multi_agent_system.core.database import DatabaseManager
from src.multi_agent_system.core.permissions import get_role_routes, require_role
from tests.conftest import TEST_DATABASE_URL

_SESSION_SECRET = "test-session-secret-32-chars-or-more"


def _build_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from src.multi_agent_system.config import Settings

        db_manager = DatabaseManager(database_url=TEST_DATABASE_URL)
        await db_manager.initialize()
        await db_manager.truncate_all()
        app.state.db_manager = db_manager
        app.state.settings = Settings()
        # routes.py 用到的 app.state 字段
        app.state.db_tool = MagicMock()
        app.state.db_tool.save_ticket = AsyncMock()
        app.state.db_tool.get_ticket = AsyncMock(return_value=None)
        app.state.db_tool.list_tickets = AsyncMock(return_value=[])
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
    app.add_middleware(SessionMiddleware, secret_key=_SESSION_SECRET)
    app.include_router(auth_router, prefix="/api")
    app.include_router(
        user_router, prefix="/api", dependencies=[Depends(require_login)]
    )
    app.include_router(
        biz_router, prefix="/api", dependencies=[Depends(require_login)]
    )
    return app


@pytest.fixture
def app():
    return _build_app()


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str = "alice") -> dict:
    """注册用户并返回 user 对象（role 必为 user）。"""
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": "secret123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["user"]


# ============================================================
# 注册用户默认 role=user，不允许通过 body 提权
# ============================================================


def test_register_creates_user_role(client: TestClient) -> None:
    """注册接口创建的用户 role 必为 user。"""
    user = _register(client, "alice")
    assert user["role"] == "user"


def test_register_ignores_role_in_body(client: TestClient) -> None:
    """body 塞 role=admin 不生效（Pydantic extra=ignore + ORM 强制 user）。"""
    # 先注册一个用户（占用 alice），logout 后再注册 bob
    _register(client, "alice")
    client.post("/api/auth/logout")
    resp = client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "secret123", "role": "admin"},
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["role"] == "user"


# ============================================================
# user 视角：403 拦截
# ============================================================


def test_user_role_cannot_access_reviews(client: TestClient) -> None:
    _register(client, "alice")
    resp = client.get("/api/reviews/queue")
    assert resp.status_code == 403


def test_user_role_cannot_access_knowledge(client: TestClient) -> None:
    _register(client, "alice")
    resp = client.get("/api/knowledge")
    assert resp.status_code == 403


def test_user_role_cannot_access_settings(client: TestClient) -> None:
    _register(client, "alice")
    resp = client.get("/api/settings")
    assert resp.status_code == 403


def test_user_role_can_access_tickets(client: TestClient) -> None:
    """普通 user 调 /api/tickets 应 200（不在 require_role 限制范围）。"""
    _register(client, "alice")
    resp = client.get("/api/tickets")
    assert resp.status_code == 200


def test_user_role_can_access_users_me(client: TestClient) -> None:
    """普通 user 调 /api/users/me 应 200。"""
    _register(client, "alice")
    resp = client.get("/api/users/me")
    assert resp.status_code == 200
    assert resp.json()["role"] == "user"


# ============================================================
# require_role 单元测试
# ============================================================


def test_require_role_rejects_invalid_role_name() -> None:
    with pytest.raises(ValueError, match="未知角色"):
        require_role("superuser")


def test_require_role_requires_at_least_one_role() -> None:
    with pytest.raises(ValueError, match="至少"):
        require_role()


def test_role_route_permissions_match_v22_boundary() -> None:
    """v2.2 前端可见路由边界：员工、服务台、系统运维三端分离。"""
    admin_routes = set(get_role_routes("admin"))
    developer_routes = set(get_role_routes("developer"))
    user_routes = set(get_role_routes("user"))

    assert "/dashboard" in admin_routes
    assert "/dashboard" not in user_routes
    assert "/dashboard" not in developer_routes
    assert "/my" in user_routes
    assert "/my" not in admin_routes
    assert "/my" not in developer_routes
    assert "/settings" not in admin_routes
    assert "/settings" in developer_routes
    assert "/monitor" not in admin_routes
    assert "/monitor" in developer_routes
    assert "/reviews" in admin_routes
    assert "/reviews" not in developer_routes
    assert "/knowledge" in admin_routes
    assert "/knowledge" not in developer_routes
    assert "/tickets" in admin_routes
    assert "/tickets" not in developer_routes
    assert "/admin/users" not in admin_routes
    assert "/admin/users" in developer_routes
    assert "/dev/prompts" not in admin_routes
    assert "/dev/prompts" in developer_routes


# ============================================================
# 演示模式视为 admin 放行
# ============================================================


def test_demo_mode_treats_user_as_admin(
    app: FastAPI, client: TestClient, monkeypatch
) -> None:
    """auth_enabled=false 时所有路由对匿名放行（视作 admin）。

    用 setenv 让 require_login / require_role 实例化的 Settings 都读到
    False；monkeypatch 类属性对 pydantic-settings 实例化无效。
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")

    resp = client.get("/api/settings")
    assert resp.status_code == 200


def test_config_fallback_admin_username_logs_in_as_developer(
    client: TestClient, monkeypatch
) -> None:
    """配置兜底用户名可为 admin，但登录后角色必须是系统运维 developer。"""
    monkeypatch.setenv("AUTH_USERNAME", "admin")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("secret123"))

    login_resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret123"},
    )
    assert login_resp.status_code == 200, login_resp.text

    me_resp = client.get("/api/auth/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["role"] == "developer"

    permissions_resp = client.get("/api/users/me/permissions")
    assert permissions_resp.status_code == 200
    body = permissions_resp.json()
    assert body["role"] == "developer"
    assert "/admin/users" in body["routes"]
    assert "/tickets" not in body["routes"]


# ============================================================
# developer / admin 视角放行（v2.0 设计 3 角色：user/admin/developer）
# ============================================================


def _build_minimal_role_app() -> FastAPI:
    """构造最小化测试 app，只挂 2 个带 require_role 的端点。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key=_SESSION_SECRET)
    r = APIRouter(prefix="/test")

    @r.get("/dev-or-admin", dependencies=[Depends(require_role("developer", "admin"))])
    async def dev_or_admin():
        return {"ok": True}

    @r.get("/admin-only", dependencies=[Depends(require_role("admin"))])
    async def admin_only():
        return {"ok": True}

    app.include_router(r, prefix="/api")
    return app


def test_developer_session_can_access_dev_route(monkeypatch) -> None:
    """session 里 role=developer → /dev-or-admin 返回 200。"""
    from src.multi_agent_system.core import auth as auth_mod

    def fake_get_current_user(request):
        return {"username": "x", "role": "developer"}

    monkeypatch.setattr(auth_mod, "get_current_user", fake_get_current_user)

    app = _build_minimal_role_app()
    with TestClient(app) as tc:
        resp = tc.get("/api/test/dev-or-admin")
        assert resp.status_code == 200
        # developer 不能进 admin-only
        resp = tc.get("/api/test/admin-only")
        assert resp.status_code == 403


def test_admin_session_can_access_admin_route(monkeypatch) -> None:
    """session 里 role=admin → /admin-only 返回 200。"""
    from src.multi_agent_system.core import auth as auth_mod

    def fake_get_current_user(request):
        return {"username": "admin", "role": "admin"}

    monkeypatch.setattr(auth_mod, "get_current_user", fake_get_current_user)

    app = _build_minimal_role_app()
    with TestClient(app) as tc:
        resp = tc.get("/api/test/admin-only")
        assert resp.status_code == 200
        resp = tc.get("/api/test/dev-or-admin")
        assert resp.status_code == 200  # admin 也在 dev-or-admin 集合


def test_unauthenticated_returns_401(monkeypatch) -> None:
    """无 session → require_role 优先返回 401（不是 403）。"""
    from src.multi_agent_system.core import auth as auth_mod

    def fake_get_current_user(request):
        return None

    monkeypatch.setattr(auth_mod, "get_current_user", fake_get_current_user)

    app = _build_minimal_role_app()
    with TestClient(app) as tc:
        resp = tc.get("/api/test/admin-only")
        assert resp.status_code == 401
