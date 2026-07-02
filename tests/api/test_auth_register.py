"""U-02 用户自助注册接口测试。"""

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
    """构建带 auth + user 路由的测试 app。"""

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


# ============================================================
# 注册成功路径
# ============================================================


def test_register_success_creates_user_and_session(client: TestClient) -> None:
    """合法字段注册：201 + 用户对象 + session 写入。"""
    resp = client.post(
        "/api/auth/register",
        json={
            "username": "alice",
            "password": "secret123",
            "nickname": "Alice",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "user" in body
    user = body["user"]
    assert user["username"] == "alice"
    assert user["nickname"] == "Alice"
    assert user["vip_level"] == 0
    assert user["status"] == "active"
    assert user["user_id"]  # user_id 存在
    assert "password_hash" not in user

    # session 已写入：调用 /users/me 能拿到注册用户
    me = client.get("/api/users/me")
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


def test_register_success_without_nickname_uses_username(client: TestClient) -> None:
    """nickname 缺省时回退到 username。"""
    resp = client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "secret123"},
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["nickname"] == "bob"


def test_register_initial_values(client: TestClient) -> None:
    """新用户的初始 vip_level=0、status=active、preferred_categories=[]。"""
    resp = client.post(
        "/api/auth/register",
        json={"username": "carol", "password": "secret123"},
    )
    assert resp.status_code == 201
    user = resp.json()["user"]
    assert user["vip_level"] == 0
    assert user["status"] == "active"
    assert user["preferred_categories"] == []


# ============================================================
# 用户名重复 409
# ============================================================


def test_register_duplicate_username_returns_409(client: TestClient) -> None:
    """第二次注册同名用户返回 409 + username_taken。"""
    payload = {"username": "dave", "password": "secret123"}
    first = client.post("/api/auth/register", json=payload)
    assert first.status_code == 201

    # 清掉第一个 session 再注册
    client.post("/api/auth/logout")

    second = client.post("/api/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["error"] == "username_taken"


# ============================================================
# 校验失败 422
# ============================================================


def test_register_short_password_returns_422(client: TestClient) -> None:
    """密码 < 8 位由 Pydantic 拦截，返回 422。"""
    resp = client.post(
        "/api/auth/register",
        json={"username": "eve", "password": "abc"},
    )
    assert resp.status_code == 422


def test_register_invalid_username_returns_422(client: TestClient) -> None:
    """用户名包含非法字符（!）返回 422 + invalid_username。"""
    resp = client.post(
        "/api/auth/register",
        json={"username": "alice!", "password": "secret123"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_username"


def test_register_short_username_returns_422(client: TestClient) -> None:
    """用户名 < 3 字符返回 422。"""
    resp = client.post(
        "/api/auth/register",
        json={"username": "ab", "password": "secret123"},
    )
    # Pydantic min_length=3 触发 422
    assert resp.status_code == 422


# ============================================================
# auth_enabled=false 也要能注册
# ============================================================


def test_register_works_when_auth_disabled(
    app: FastAPI, client: TestClient, monkeypatch
) -> None:
    """演示模式下注册接口照常工作。"""
    from src.multi_agent_system.core import auth as auth_module

    # 猴补 Settings.auth_enabled -> False
    real_settings_cls = auth_module.Settings

    class _DisabledSettings(real_settings_cls):
        auth_enabled: bool = False

    monkeypatch.setattr(auth_module, "Settings", _DisabledSettings)

    resp = client.post(
        "/api/auth/register",
        json={"username": "frank", "password": "secret123"},
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["username"] == "frank"
