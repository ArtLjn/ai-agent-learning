"""D-02 Prompt 版本管理测试。

覆盖：
- create → list → activate → 再 activate 旧版本（旧 active is_active=false）
- 同 (agent_name, version) 唯一约束（应用层 version 自增，绕过此约束）
- agent_name 不在 5 个白名单 → 422
- diff 接口返回 unified diff 字符串
- user 角色 403
- 未登录 401
"""

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.admin_prompts import router as admin_prompts_router
from src.multi_agent_system.api.auth_routes import router as auth_router
from src.multi_agent_system.api.user_routes import router as user_router
from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.auth import require_login
from src.multi_agent_system.core.database import DatabaseManager

from tests.conftest import TEST_DATABASE_URL

_SESSION_SECRET = "test-admin-prompts-secret"


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
    app.include_router(admin_prompts_router, prefix="/api")
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


def test_list_unauthenticated_returns_401(client: TestClient) -> None:
    _logout(client)
    resp = client.get("/api/admin/prompts/classify/versions")
    assert resp.status_code == 401


def test_list_user_role_returns_403(client: TestClient) -> None:
    _register(client, "normaluser")
    resp = client.get("/api/admin/prompts/classify/versions")
    assert resp.status_code == 403


def test_invalid_agent_name_returns_422(client: TestClient, app: FastAPI) -> None:
    """agent_name 不在 5 个白名单 → 422。"""
    admin = _register(client, "admin1")
    _promote_to_admin(client, app, admin["user_id"])
    resp = client.get("/api/admin/prompts/unknown_agent/versions")
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"]["error"] == "invalid_agent_name"
    assert set(body["detail"]["valid"]) == {
        "intent", "classify", "process", "review", "coordinator"
    }


# ============================================================
# 主流程：create → list → activate → 切换 active
# ============================================================


def test_create_list_activate_cycle(client: TestClient, app: FastAPI) -> None:
    """admin: create v1 → list 显示 1 条 → 创建 v2 自动 active → v1 is_active=false。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    # 创建 v1（activate=true）
    resp = client.post(
        "/api/admin/prompts/classify/versions",
        json={"template": "你是分类器 v1", "note": "v1 baseline"},
    )
    assert resp.status_code == 201, resp.text
    v1 = resp.json()
    assert v1["version"] == 1
    assert v1["is_active"] is True
    assert v1["template"] == "你是分类器 v1"

    # list
    resp = client.get("/api/admin/prompts/classify/versions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["version"] == 1
    assert body["items"][0]["is_active"] is True

    # 创建 v2（activate=true）→ v1 应自动失活
    resp = client.post(
        "/api/admin/prompts/classify/versions",
        json={"template": "你是分类器 v2", "note": "v2 改进版"},
    )
    assert resp.status_code == 201
    v2 = resp.json()
    assert v2["version"] == 2
    assert v2["is_active"] is True

    resp = client.get("/api/admin/prompts/classify/versions")
    items = {it["version"]: it for it in resp.json()["items"]}
    assert items[1]["is_active"] is False
    assert items[2]["is_active"] is True


def test_activate_old_version_disables_current(
    client: TestClient, app: FastAPI
) -> None:
    """激活旧版本：当前 active 失活，目标版本激活。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    # 创建 v1 v2 v3
    for i in range(1, 4):
        client.post(
            "/api/admin/prompts/review/versions",
            json={"template": f"v{i} template"},
        )

    # 当前 v3 是 active
    resp = client.get("/api/admin/prompts/review/active")
    assert resp.json()["active"]["version"] == 3

    # 激活 v1
    resp = client.post("/api/admin/prompts/review/versions/1/activate")
    assert resp.status_code == 200
    assert resp.json()["version"] == 1
    assert resp.json()["is_active"] is True

    # v3 应该失活
    items = {
        it["version"]: it
        for it in client.get("/api/admin/prompts/review/versions").json()["items"]
    }
    assert items[1]["is_active"] is True
    assert items[2]["is_active"] is False
    assert items[3]["is_active"] is False


def test_activate_nonexistent_returns_404(
    client: TestClient, app: FastAPI
) -> None:
    """激活不存在的版本 → 404。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    resp = client.post("/api/admin/prompts/classify/versions/999/activate")
    assert resp.status_code == 404


def test_create_without_activate_keeps_current(
    client: TestClient, app: FastAPI
) -> None:
    """activate=false 不切换 active。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    client.post(
        "/api/admin/prompts/intent/versions",
        json={"template": "v1", "activate": True},
    )
    # 创建 v2 但不激活
    resp = client.post(
        "/api/admin/prompts/intent/versions",
        json={"template": "v2 草稿", "activate": False},
    )
    assert resp.status_code == 201
    assert resp.json()["is_active"] is False

    # v1 仍是 active
    active = client.get("/api/admin/prompts/intent/active").json()["active"]
    assert active["version"] == 1


# ============================================================
# Diff 接口
# ============================================================


def test_diff_returns_unified_diff_string(
    client: TestClient, app: FastAPI
) -> None:
    """diff 接口返回 unified diff 字符串。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    client.post(
        "/api/admin/prompts/process/versions",
        json={"template": "line1\nline2\nline3"},
    )
    client.post(
        "/api/admin/prompts/process/versions",
        json={"template": "line1\nline2 modified\nline3\nline4"},
    )

    resp = client.get("/api/admin/prompts/process/diff?from=1&to=2")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["from_version"] == 1
    assert body["to_version"] == 2
    assert "line2" in body["diff"]
    assert body["has_diff"] is True


def test_diff_identical_versions_empty(
    client: TestClient, app: FastAPI
) -> None:
    """相同内容 → has_diff=False。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    client.post(
        "/api/admin/prompts/process/versions",
        json={"template": "same content"},
    )
    client.post(
        "/api/admin/prompts/process/versions",
        json={"template": "same content"},
    )

    resp = client.get("/api/admin/prompts/process/diff?from=1&to=2")
    assert resp.status_code == 200
    assert resp.json()["has_diff"] is False


def test_diff_missing_version_returns_404(
    client: TestClient, app: FastAPI
) -> None:
    """diff 不存在的版本 → 404。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    client.post(
        "/api/admin/prompts/classify/versions",
        json={"template": "v1"},
    )
    resp = client.get("/api/admin/prompts/classify/diff?from=1&to=999")
    assert resp.status_code == 404


# ============================================================
# DatabaseManager 直接测试
# ============================================================


@pytest.mark.asyncio
async def test_db_create_prompt_version_assigns_sequential_version(
    client: TestClient, app: FastAPI
) -> None:
    """DB 层：连续创建 3 个版本，version 自增 1/2/3。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    db: DatabaseManager = app.state.db_manager
    v1 = await db.create_prompt_version("classify", "t1")
    v2 = await db.create_prompt_version("classify", "t2")
    v3 = await db.create_prompt_version("classify", "t3")
    assert (v1["version"], v2["version"], v3["version"]) == (1, 2, 3)
