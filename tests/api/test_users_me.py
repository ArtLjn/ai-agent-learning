"""U-03 用户信息管理接口测试（GET / PATCH /api/users/me）。"""

from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.auth_routes import router as auth_router
from src.multi_agent_system.api.user_routes import router as user_router
from src.multi_agent_system.core.auth import require_login
from src.multi_agent_system.core.database import DatabaseManager
from tests.conftest import TEST_DATABASE_URL

_SESSION_SECRET = "test-session-secret-32-chars-or-more"


def _build_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_manager = DatabaseManager(database_url=TEST_DATABASE_URL)
        await db_manager.initialize()
        await db_manager.truncate_all()
        app.state.db_manager = db_manager
        yield
        await db_manager.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=_SESSION_SECRET)
    app.include_router(auth_router, prefix="/api")
    app.include_router(
        user_router, prefix="/api", dependencies=[Depends(require_login)]
    )
    return app


@pytest.fixture
def app():
    return _build_app()


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        yield c


def _register_and_keep_session(client: TestClient, username: str = "alice") -> dict:
    """注册一个用户并保留 session，返回 user 对象。"""
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": "secret123", "nickname": username.title()},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["user"]


# ============================================================
# 未登录 401
# ============================================================


def test_get_me_unauthenticated_returns_401(client: TestClient) -> None:
    """无 session 调用 /users/me 返回 401。"""
    # 清掉 TestClient 默认创建的空 session
    client.post("/api/auth/logout")
    resp = client.get("/api/users/me")
    assert resp.status_code == 401


def test_patch_me_unauthenticated_returns_401(client: TestClient) -> None:
    client.post("/api/auth/logout")
    resp = client.patch("/api/users/me", json={"nickname": "x"})
    assert resp.status_code == 401


# ============================================================
# 正常路径
# ============================================================


def test_get_me_returns_logged_in_user(client: TestClient) -> None:
    """已注册并登录：GET /users/me 返回完整信息（无 password_hash）。"""
    user = _register_and_keep_session(client)
    resp = client.get("/api/users/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == user["user_id"]
    assert body["username"] == "alice"
    assert body["nickname"] == "Alice"
    assert body["vip_level"] == 0
    assert body["preferred_categories"] == []
    assert body["department"] is None
    assert body["position"] is None
    assert "password_hash" not in body


def test_patch_me_updates_nickname(client: TestClient) -> None:
    """修改 nickname 持久化。"""
    _register_and_keep_session(client)

    resp = client.patch("/api/users/me", json={"nickname": "Alice L"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nickname"] == "Alice L"

    # 再 GET 确认持久化
    me = client.get("/api/users/me")
    assert me.json()["nickname"] == "Alice L"


def test_patch_me_updates_preferred_categories(client: TestClient) -> None:
    """preferred_categories 数组持久化。"""
    _register_and_keep_session(client)

    resp = client.patch(
        "/api/users/me",
        json={"preferred_categories": ["technical", "billing"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["preferred_categories"]) == {"technical", "billing"}

    me = client.get("/api/users/me")
    assert set(me.json()["preferred_categories"]) == {"technical", "billing"}


def test_patch_me_updates_contact(client: TestClient) -> None:
    """contact 字段持久化。"""
    _register_and_keep_session(client)
    resp = client.patch(
        "/api/users/me", json={"contact": "alice@example.com"}
    )
    assert resp.status_code == 200
    assert resp.json()["contact"] == "alice@example.com"


def test_patch_me_updates_employee_service_profile_fields(client: TestClient) -> None:
    """员工服务档案允许维护部门、岗位和偏好服务类型。"""
    _register_and_keep_session(client)

    resp = client.patch(
        "/api/users/me",
        json={
            "nickname": "Alice Service",
            "contact": "alice@example.com",
            "department": "研发中心",
            "position": "后端工程师",
            "preferred_categories": ["technical", "inquiry"],
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nickname"] == "Alice Service"
    assert body["department"] == "研发中心"
    assert body["position"] == "后端工程师"
    assert set(body["preferred_categories"]) == {"technical", "inquiry"}
    assert "password_hash" not in body


def test_patch_me_partial_update_keeps_other_fields(client: TestClient) -> None:
    """部分更新：只改一个字段，其他字段保留。"""
    _register_and_keep_session(client)
    # 先建立基线
    client.patch(
        "/api/users/me",
        json={"nickname": "First", "contact": "first@example.com"},
    )

    # 再只改 nickname
    resp = client.patch("/api/users/me", json={"nickname": "Second"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["nickname"] == "Second"
    assert body["contact"] == "first@example.com"  # 保留


# ============================================================
# 不可改字段
# ============================================================


def test_patch_me_user_id_in_body_does_not_change_db(client: TestClient, app: FastAPI) -> None:
    """请求体含 user_id 时被忽略（Pydantic 默认 extra=ignore）。"""
    user = _register_and_keep_session(client)
    original_user_id = user["user_id"]

    # 客户端塞 user_id 试图改
    resp = client.patch(
        "/api/users/me",
        json={"user_id": "U-HACKED", "nickname": "Hacker"},
    )
    # Pydantic 默认 extra='ignore'，所以 user_id 被丢弃，nickname 更新成功
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == original_user_id  # 未被改
    assert body["nickname"] == "Hacker"

    # DB 中 user_id 也没变
    db_user = client.portal.call(app.state.db_manager.get_user, original_user_id)
    assert db_user is not None
    assert db_user["user_id"] == original_user_id


# ============================================================
# preferred_categories 校验
# ============================================================


def test_patch_me_invalid_category_returns_422(client: TestClient) -> None:
    """preferred_categories 含未知值返回 422 + invalid_category。"""
    _register_and_keep_session(client)
    resp = client.patch(
        "/api/users/me",
        json={"preferred_categories": ["technical", "unknown_cat"]},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid_category"
    assert "unknown_cat" in body["invalid"]


# ============================================================
# role 字段（fix-role-based-access）
# ============================================================


def test_get_me_returns_role_field(client: TestClient) -> None:
    """GET /users/me 必须返回 role 字段。"""
    _register_and_keep_session(client)
    body = client.get("/api/users/me").json()
    assert body["role"] == "user"


def test_get_me_permissions_returns_user_routes(client: TestClient) -> None:
    """普通 user 调 /users/me/permissions 返回 user 可见路由。"""
    _register_and_keep_session(client)
    resp = client.get("/api/users/me/permissions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "user"
    assert set(body["routes"]) == {"/", "/my", "/tickets", "/tickets/:id", "/profile"}
    # 不含 admin 专属路由
    for forbidden in ("/reviews", "/knowledge", "/settings", "/monitor"):
        assert forbidden not in body["routes"]


def test_get_me_permissions_after_role_change(
    client: TestClient, app: FastAPI
) -> None:
    """admin 在 DB 改 role 后，老 session 调 /permissions 应反映最新角色。"""
    user = _register_and_keep_session(client)
    # DB 改 role=admin（v2.0 设计 3 角色：user/admin/developer）
    client.portal.call(
        app.state.db_manager.update_user_role, user["user_id"], "admin"
    )
    # session 里的 role 仍是 user，但 /permissions 端点会兜底查 DB
    resp = client.get("/api/users/me/permissions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "admin"
    assert "/reviews" in body["routes"]
