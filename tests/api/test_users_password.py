"""U-04 修改密码接口测试（POST /api/users/me/password）。"""

from contextlib import asynccontextmanager

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


def _register(client: TestClient, username: str = "alice", password: str = "secret123") -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["user"]


# ============================================================
# 修改成功
# ============================================================


def test_change_password_success_clears_session(client: TestClient) -> None:
    """合法的旧密码 + 新密码：成功后 session 失效。"""
    _register(client, password="secret123")

    resp = client.post(
        "/api/users/me/password",
        json={"old_password": "secret123", "new_password": "newpass456"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["redirect"] == "/login"

    # session 已清：再调 /users/me 应 401
    me = client.get("/api/users/me")
    assert me.status_code == 401


def test_change_password_new_hash_differs_from_old(
    client: TestClient, app: FastAPI
) -> None:
    """DB 中 password_hash 已替换为新密码的哈希。"""
    user = _register(client, password="secret123")
    user_id = user["user_id"]

    before = client.portal.call(app.state.db_manager.get_user, user_id)
    assert before is not None
    old_hash = before["password_hash"]

    client.post(
        "/api/users/me/password",
        json={"old_password": "secret123", "new_password": "newpass456"},
    )

    after = client.portal.call(app.state.db_manager.get_user, user_id)
    assert after is not None
    new_hash = after["password_hash"]
    assert new_hash
    assert new_hash != old_hash


# ============================================================
# 旧密码错误 401
# ============================================================


def test_change_password_wrong_old_returns_401(client: TestClient) -> None:
    """旧密码错误返回 401 + invalid_credentials。"""
    _register(client, password="secret123")

    resp = client.post(
        "/api/users/me/password",
        json={"old_password": "WRONG", "new_password": "newpass456"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_credentials"


# ============================================================
# 新密码与旧密码相同 422
# ============================================================


def test_change_password_same_as_old_returns_422(client: TestClient) -> None:
    """新密码与旧密码相同返回 422 + password_same_as_old。"""
    _register(client, password="secret123")

    resp = client.post(
        "/api/users/me/password",
        json={"old_password": "secret123", "new_password": "secret123"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "password_same_as_old"


# ============================================================
# 新密码强度不足 422
# ============================================================


def test_change_password_weak_new_returns_422(client: TestClient) -> None:
    """新密码 < 8 字符返回 422（Pydantic 拦截）。"""
    _register(client, password="secret123")

    resp = client.post(
        "/api/users/me/password",
        json={"old_password": "secret123", "new_password": "abc"},
    )
    assert resp.status_code == 422


# ============================================================
# 未登录 401
# ============================================================


def test_change_password_unauthenticated_returns_401(client: TestClient) -> None:
    client.post("/api/auth/logout")
    resp = client.post(
        "/api/users/me/password",
        json={"old_password": "x", "new_password": "y"},
    )
    assert resp.status_code == 401
