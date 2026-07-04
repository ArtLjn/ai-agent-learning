"""A-06 系统配置查看测试。

覆盖：
- admin 角色返回 200 含 6 类配置
- user 角色 403
- 未登录 401
- 密钥字段不出现在响应里（脱敏）
- URL 完整显示
- 演示模式（auth_enabled=false）放行
"""

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.admin_config import router as admin_config_router
from src.multi_agent_system.api.auth_routes import router as auth_router
from src.multi_agent_system.api.user_routes import router as user_router
from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.auth import require_login
from src.multi_agent_system.core.database import DatabaseManager

from tests.conftest import TEST_DATABASE_URL

_SESSION_SECRET = "test-admin-config-secret"


def _build_app() -> FastAPI:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_manager = DatabaseManager(database_url=TEST_DATABASE_URL)
        await db_manager.initialize()
        await db_manager.truncate_all()
        app.state.db_manager = db_manager
        app.state.settings = Settings()
        yield
        await db_manager.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=_SESSION_SECRET)
    app.include_router(auth_router, prefix="/api")
    app.include_router(
        user_router, prefix="/api", dependencies=[Depends(require_login)]
    )
    app.include_router(admin_config_router, prefix="/api")
    return app


@pytest.fixture
def app():
    return _build_app()


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str = "alice") -> dict[str, Any]:
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
    client.post("/api/auth/logout")
    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": "secret123"},
    )
    assert resp.status_code == 200


def _promote_to_admin(client: TestClient, app: FastAPI, user_id: str) -> None:
    updated = client.portal.call(app.state.db_manager.update_user_role, user_id, "admin")
    assert updated is not None and updated["role"] == "admin"
    _relogin(client, updated["username"])


def _logout(client: TestClient) -> None:
    client.post("/api/auth/logout")


# ============================================================
# 鉴权
# ============================================================


def test_unauthenticated_returns_401(client: TestClient) -> None:
    _logout(client)
    resp = client.get("/api/admin/config")
    assert resp.status_code == 401


def test_user_role_returns_403(client: TestClient, app: FastAPI) -> None:
    """普通 user 调 /admin/config → 403。"""
    _register(client, "normaluser")
    resp = client.get("/api/admin/config")
    assert resp.status_code == 403


def test_admin_role_returns_200(client: TestClient, app: FastAPI) -> None:
    """admin 角色 → 200。"""
    user = _register(client, "theadmin")
    _promote_to_admin(client, app, user["user_id"])

    resp = client.get("/api/admin/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "llm" in body
    assert "embedding" in body
    assert "qdrant" in body
    assert "rag_service" in body
    assert "database" in body
    assert "auth" in body


# ============================================================
# 6 类配置完整性
# ============================================================


def test_config_response_has_6_categories(client: TestClient, app: FastAPI) -> None:
    """响应必须包含 6 类配置（设计依据 tasks.md 5.1）。"""
    user = _register(client, "theadmin")
    _promote_to_admin(client, app, user["user_id"])

    body = client.get("/api/admin/config").json()
    expected_keys = {"llm", "embedding", "qdrant", "rag_service", "database", "auth"}
    assert expected_keys.issubset(body.keys())


def test_llm_category_fields(client: TestClient, app: FastAPI) -> None:
    """LLM 类包含 base_url / model / api_key_configured。"""
    user = _register(client, "theadmin")
    _promote_to_admin(client, app, user["user_id"])

    llm = client.get("/api/admin/config").json()["llm"]
    assert "base_url" in llm
    assert "model" in llm
    assert "api_key_configured" in llm
    assert "fallback_model" in llm
    assert "model_routes" in llm
    assert isinstance(llm["api_key_configured"], bool)


def test_database_category_masks_password(client: TestClient, app: FastAPI) -> None:
    """database 类只暴露 driver/host/port/db，不返回 username/password 原值。"""
    user = _register(client, "theadmin")
    _promote_to_admin(client, app, user["user_id"])

    db_cfg = client.get("/api/admin/config").json()["database"]
    assert "driver" in db_cfg
    assert "host" in db_cfg
    assert "port" in db_cfg
    assert "database" in db_cfg
    # 只暴露 configured 布尔，不暴露原值
    assert "username_configured" in db_cfg
    assert "password_configured" in db_cfg
    # 不应出现明文字段
    assert "username" not in db_cfg
    assert "password" not in db_cfg
    assert "url" not in db_cfg


# ============================================================
# 脱敏：密钥字段省略
# ============================================================


def test_secret_fields_not_in_response(client: TestClient, app: FastAPI) -> None:
    """密钥类字段（API_KEY / PASSWORD / SECRET）不应出现在响应中。

    design.md 决策 4：直接省略字段，不返回 "***"。
    """
    user = _register(client, "theadmin")
    _promote_to_admin(client, app, user["user_id"])

    text = client.get("/api/admin/config").text
    # 不应出现以下任何 key
    forbidden_keys = [
        "llm_api_key",
        "embedding_api_key",
        "qdrant_api_key",
        "rag_service_api_key",
        "auth_password_hash",
        "auth_session_secret",
        "database_url",
    ]
    for key in forbidden_keys:
        assert f'"{key}"' not in text, f"响应中出现了密钥字段: {key}"


def test_url_fields_visible(client: TestClient, app: FastAPI) -> None:
    """URL 类字段（非密钥）应完整显示。"""
    user = _register(client, "theadmin")
    _promote_to_admin(client, app, user["user_id"])

    body = client.get("/api/admin/config").json()
    # llm.base_url / qdrant.url / embedding.base_url 都应可见
    assert body["llm"]["base_url"]  # 非空字符串
    assert body["qdrant"]["url"]
    assert body["embedding"]["base_url"]


def test_rag_service_api_key_only_returns_configured_flag(
    client: TestClient, app: FastAPI
) -> None:
    """rag_service 字段含 api_key_configured 布尔，不返回 api_key 原值。

    密钥全脱敏：仅返回 configured 标志，原值绝不进响应。
    """
    user = _register(client, "theadmin")
    _promote_to_admin(client, app, user["user_id"])

    rag_cfg = client.get("/api/admin/config").json()["rag_service"]
    assert "api_key_configured" in rag_cfg
    assert isinstance(rag_cfg["api_key_configured"], bool)
    # 不应出现 api_key 原值字段
    assert "api_key" not in rag_cfg


def test_auth_category_does_not_leak_secret(client: TestClient, app: FastAPI) -> None:
    """auth 类只暴露开关，不暴露 hash/secret 原值。"""
    user = _register(client, "theadmin")
    _promote_to_admin(client, app, user["user_id"])

    auth_cfg = client.get("/api/admin/config").json()["auth"]
    assert "auth_enabled" in auth_cfg
    assert "password_hash_configured" in auth_cfg
    assert "session_secret_configured" in auth_cfg
    # 不应出现 hash/secret 原值
    assert "password_hash" not in auth_cfg
    assert "session_secret" not in auth_cfg


# ============================================================
# 演示模式放行
# ============================================================


def test_demo_mode_accessible(monkeypatch) -> None:
    """AUTH_ENABLED=false 时视为 admin，无登录也能访问。"""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = _build_app()
    with TestClient(app) as tc:
        resp = tc.get("/api/admin/config")
        assert resp.status_code == 200
        body = resp.json()
        assert "llm" in body
