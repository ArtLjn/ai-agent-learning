"""A-04 用户管理测试：admin 列表 / 改 role / 改 status / 自我保护。"""

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.admin_users import router as admin_users_router
from src.multi_agent_system.api.auth_routes import router as auth_router
from src.multi_agent_system.api.user_routes import router as user_router
from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.auth import require_login
from src.multi_agent_system.core.database import DatabaseManager

from tests.conftest import TEST_DATABASE_URL

_SESSION_SECRET = "test-admin-users-secret"


def _build_app() -> FastAPI:
    """构造最小化测试 app，含 register/me/admin-users 三个路由组。

    默认依赖 Settings.auth_enabled=True；演示模式由测试用 monkeypatch 设置
    AUTH_ENABLED=false，避免 _build_app 直接改 os.environ 污染其他测试。
    """
    from contextlib import asynccontextmanager

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
    app.include_router(admin_users_router, prefix="/api")
    return app


@pytest.fixture
def app():
    return _build_app()


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str = "alice") -> dict[str, Any]:
    """注册用户并保留 session。

    注意：每次 register 都会覆盖 session（自动登录新用户）。
    测试中需要 admin 视角时，应先 register 所有非 admin 用户，最后再
    register admin 并 _promote_to_admin，避免后续 register 覆盖 session。
    """
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


def _relogin(client: TestClient, username: str) -> None:
    """重新登录指定用户（admin 后台改 role 后必用）。"""
    client.post("/api/auth/logout")
    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": "secret123"},
    )
    assert resp.status_code == 200, f"重新登录失败: {resp.text}"


def _promote_to_admin(client: TestClient, app: FastAPI, user_id: str) -> None:
    """DB 层面把 user_id 升为 admin，然后重新 login 让 session 拿到新 role。"""
    updated = client.portal.call(
        app.state.db_manager.update_user_role, user_id, "admin"
    )
    assert updated is not None and updated["role"] == "admin"
    username = updated["username"]
    client.post("/api/auth/logout")
    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": "secret123"},
    )
    assert resp.status_code == 200, f"重新登录失败: {resp.text}"


def _logout(client: TestClient) -> None:
    client.post("/api/auth/logout")


# ============================================================
# 鉴权：未登录 401，user 角色 403，admin 才放行
# ============================================================


def test_list_users_unauthenticated_returns_401(client: TestClient) -> None:
    _logout(client)
    resp = client.get("/api/admin/users")
    assert resp.status_code == 401


def test_list_users_user_role_returns_403(
    client: TestClient, app: FastAPI
) -> None:
    """注册的 user 调 /admin/users 返回 403。"""
    _register(client, "normaluser")
    resp = client.get("/api/admin/users")
    assert resp.status_code == 403


def test_list_users_admin_role_returns_200(
    client: TestClient, app: FastAPI
) -> None:
    """admin 角色可以列用户。"""
    user = _register(client, "admin1")
    _promote_to_admin(client, app, user["user_id"])

    resp = client.get("/api/admin/users")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    assert any(item["user_id"] == user["user_id"] for item in body["items"])


# ============================================================
# 筛选 + 分页
# ============================================================


def test_list_users_filters_by_role(client: TestClient, app: FastAPI) -> None:
    """role=developer 筛选只返回 developer。"""
    # 先注册所有非 admin 用户
    _register(client, "user1")
    _register(client, "user2")
    dev = _register(client, "dev1")
    # 把 dev1 升为 developer
    client.portal.call(
        app.state.db_manager.update_user_role, dev["user_id"], "developer"
    )
    # 最后注册 admin 并 promote（避免覆盖 session）
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    resp = client.get("/api/admin/users", params={"role": "developer"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert all(item["role"] == "developer" for item in body["items"])
    assert any(item["user_id"] == dev["user_id"] for item in body["items"])


def test_list_users_keyword_search(client: TestClient, app: FastAPI) -> None:
    """keyword 模糊匹配 username。"""
    # 先注册所有非 admin 用户
    _register(client, "alice")
    _register(client, "bob")
    _register(client, "alex")
    # 最后注册 admin
    admin = _register(client, "rootadmin")
    _promote_to_admin(client, app, admin["user_id"])

    resp = client.get("/api/admin/users", params={"keyword": "al"})
    assert resp.status_code == 200, resp.text
    usernames = {item["username"] for item in resp.json()["items"]}
    assert "alice" in usernames
    assert "alex" in usernames
    assert "bob" not in usernames


# ============================================================
# PATCH：改 role + status
# ============================================================


def test_patch_role_to_developer(client: TestClient, app: FastAPI) -> None:
    """admin 把 user 升为 developer。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])
    target = _register(client, "tobedev")
    _logout(client)
    client.post(
        "/api/auth/login",
        json={"username": "theadmin", "password": "secret123"},
    )

    resp = client.patch(
        f"/api/admin/users/{target['user_id']}",
        json={"role": "developer"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "developer"

    # DB 落盘校验
    db_user = client.portal.call(app.state.db_manager.get_user, target["user_id"])
    assert db_user["role"] == "developer"


def test_patch_role_to_admin(client: TestClient, app: FastAPI) -> None:
    """admin 把 user 升为 admin。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])
    target = _register(client, "tobeadmin")
    _logout(client)
    client.post(
        "/api/auth/login",
        json={"username": "theadmin", "password": "secret123"},
    )

    resp = client.patch(
        f"/api/admin/users/{target['user_id']}",
        json={"role": "admin"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_patch_status_ban_then_unban(client: TestClient, app: FastAPI) -> None:
    """封禁 + 解封用户。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])
    target = _register(client, "toban")
    _logout(client)
    client.post(
        "/api/auth/login",
        json={"username": "theadmin", "password": "secret123"},
    )

    # 封禁
    resp = client.patch(
        f"/api/admin/users/{target['user_id']}",
        json={"status": "banned"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "banned"
    # 解封
    resp = client.patch(
        f"/api/admin/users/{target['user_id']}",
        json={"status": "active"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


# ============================================================
# 自我保护 + 输入校验
# ============================================================


def test_cannot_modify_self_role(client: TestClient, app: FastAPI) -> None:
    """admin 不能改自己的 role（防降权 / 误操作）。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    resp = client.patch(
        f"/api/admin/users/{admin['user_id']}",
        json={"role": "user"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "cannot_modify_self"


def test_patch_invalid_role_returns_422(
    client: TestClient, app: FastAPI
) -> None:
    """传 reviewer（已删除）或其他非法 role → 422。"""
    target = _register(client, "totarget")
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    resp = client.patch(
        f"/api/admin/users/{target['user_id']}",
        json={"role": "reviewer"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_role"


def test_patch_empty_body_returns_422(
    client: TestClient, app: FastAPI
) -> None:
    """空 body（role 和 status 都没传）→ 422。"""
    target = _register(client, "totarget")
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    resp = client.patch(
        f"/api/admin/users/{target['user_id']}", json={}
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "empty_update"


def test_patch_nonexistent_user_returns_404(
    client: TestClient, app: FastAPI
) -> None:
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    resp = client.patch(
        "/api/admin/users/U-nonexistent-xxx",
        json={"role": "developer"},
    )
    assert resp.status_code == 404


def test_patch_user_role_cannot_modify_others(
    client: TestClient, app: FastAPI
) -> None:
    """user 角色调 PATCH /admin/users/{id} → 403。"""
    user = _register(client, "normaluser")
    other = _register(client, "otheruser")

    resp = client.patch(
        f"/api/admin/users/{other['user_id']}",
        json={"role": "developer"},
    )
    assert resp.status_code == 403


# ============================================================
# 演示模式放行
# ============================================================


def test_demo_mode_admin_endpoints_accessible(monkeypatch) -> None:
    """AUTH_ENABLED=false 时视为 admin，所有 admin/* 接口放行。"""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = _build_app()
    with TestClient(app) as tc:
        # 列表
        resp = tc.get("/api/admin/users")
        assert resp.status_code == 200
