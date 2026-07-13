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


def _promote_to_developer(client: TestClient, app: FastAPI, user_id: str) -> None:
    updated = client.portal.call(
        app.state.db_manager.update_user_role, user_id, "developer"
    )
    assert updated is not None and updated["role"] == "developer"
    _relogin(client, updated["username"])


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


def test_list_admin_role_returns_403(
    client: TestClient, app: FastAPI
) -> None:
    """服务台 admin 不能进入 Prompt 策略调试接口。"""
    admin = _register(client, "serviceadmin")
    _promote_to_admin(client, app, admin["user_id"])
    resp = client.get("/api/admin/prompts/classify/versions")
    assert resp.status_code == 403


def test_list_developer_role_returns_200(
    client: TestClient, app: FastAPI
) -> None:
    """developer 角色可以列 Prompt 版本（系统运维端）。"""
    dev = _register(client, "dev1")
    _promote_to_developer(client, app, dev["user_id"])
    resp = client.get("/api/admin/prompts/classify/versions")
    assert resp.status_code == 200, resp.text


def test_create_developer_role_returns_201(
    client: TestClient, app: FastAPI
) -> None:
    """developer 角色可以新建 Prompt 版本。"""
    dev = _register(client, "dev2")
    _promote_to_developer(client, app, dev["user_id"])

    resp = client.post(
        "/api/admin/prompts/classify/versions",
        json={"template": "dev version", "note": "by developer"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["template"] == "dev version"


def test_invalid_agent_name_returns_422(client: TestClient, app: FastAPI) -> None:
    """agent_name 不在 5 个白名单 → 422。"""
    admin = _register(client, "admin1")
    _promote_to_developer(client, app, admin["user_id"])
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
    _promote_to_developer(client, app, admin["user_id"])

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
    _promote_to_developer(client, app, admin["user_id"])

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


def test_rollback_activates_previous_version(
    client: TestClient, app: FastAPI
) -> None:
    """回滚接口应激活当前 active 之前的最近版本。"""
    admin = _register(client, "theadmin")
    _promote_to_developer(client, app, admin["user_id"])

    for i in range(1, 4):
        client.post(
            "/api/admin/prompts/process/versions",
            json={"template": f"process v{i}"},
        )

    resp = client.post("/api/admin/prompts/process/rollback")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 2
    assert body["is_active"] is True

    active = client.get("/api/admin/prompts/process/active").json()["active"]
    assert active["version"] == 2


def test_rollback_without_previous_version_returns_409(
    client: TestClient, app: FastAPI
) -> None:
    """只有一个版本时不能回滚。"""
    admin = _register(client, "theadmin")
    _promote_to_developer(client, app, admin["user_id"])
    client.post(
        "/api/admin/prompts/review/versions",
        json={"template": "review v1"},
    )

    resp = client.post("/api/admin/prompts/review/rollback")
    assert resp.status_code == 409


def test_activate_nonexistent_returns_404(
    client: TestClient, app: FastAPI
) -> None:
    """激活不存在的版本 → 404。"""
    admin = _register(client, "theadmin")
    _promote_to_developer(client, app, admin["user_id"])

    resp = client.post("/api/admin/prompts/classify/versions/999/activate")
    assert resp.status_code == 404


def test_create_without_activate_keeps_current(
    client: TestClient, app: FastAPI
) -> None:
    """activate=false 不切换 active。"""
    admin = _register(client, "theadmin")
    _promote_to_developer(client, app, admin["user_id"])

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
    _promote_to_developer(client, app, admin["user_id"])

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
    _promote_to_developer(client, app, admin["user_id"])

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
    _promote_to_developer(client, app, admin["user_id"])

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
    _promote_to_developer(client, app, admin["user_id"])

    db: DatabaseManager = app.state.db_manager
    v1 = await db.create_prompt_version("classify", "t1")
    v2 = await db.create_prompt_version("classify", "t2")
    v3 = await db.create_prompt_version("classify", "t3")
    assert (v1["version"], v2["version"], v3["version"]) == (1, 2, 3)


# ============================================================
# 热重载：POST /api/admin/prompts/reload
# ============================================================


class _StubAgent:
    """最小化的 Agent 替身，仅实现 set_prompt_override。"""

    def __init__(self) -> None:
        self.override: str | None = None

    def set_prompt_override(self, template: str | None) -> None:
        self.override = template


def _attach_stub_agents(app: FastAPI) -> dict[str, _StubAgent]:
    """给 app.state 挂上 5 个 stub agent，返回 dict 供测试断言。"""
    stubs = {
        "intent": _StubAgent(),
        "classify": _StubAgent(),
        "process": _StubAgent(),
        "review": _StubAgent(),
        "coordinator": _StubAgent(),
    }
    app.state.ticket_intent_agent = stubs["intent"]
    app.state.classifier = stubs["classify"]
    app.state.processor = stubs["process"]
    app.state.reviewer = stubs["review"]
    app.state.coordinator = stubs["coordinator"]
    return stubs


def test_reload_injects_active_prompt_into_agents(
    client: TestClient, app: FastAPI
) -> None:
    """新建 v2 + activate → POST /reload → 5 个 stub agent.override 都被刷新。"""
    admin = _register(client, "theadmin")
    _promote_to_developer(client, app, admin["user_id"])
    stubs = _attach_stub_agents(app)

    # 给 classify 和 review 各建一个版本（自动 activate）
    client.post(
        "/api/admin/prompts/classify/versions",
        json={"template": "classify v1", "activate": True},
    )
    client.post(
        "/api/admin/prompts/review/versions",
        json={"template": "review v1", "activate": True},
    )

    resp = client.post("/api/admin/prompts/reload")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # classify 和 review 应该被 reload
    assert body["reloaded"]["classify"] == 1
    assert body["reloaded"]["review"] == 1
    # stub 的 override 应该被设为对应 template
    assert stubs["classify"].override == "classify v1"
    assert stubs["review"].override == "review v1"
    # intent/process/coordinator 无 active 版本，应该在 skipped 里且 override 为 None
    assert "intent" in body["skipped"]
    assert "process" in body["skipped"]
    assert "coordinator" in body["skipped"]
    assert stubs["intent"].override is None


def test_reload_picks_up_newly_activated_version(
    client: TestClient, app: FastAPI
) -> None:
    """激活 v2 后 reload → agent.override 切换到 v2 template。"""
    admin = _register(client, "theadmin")
    _promote_to_developer(client, app, admin["user_id"])
    stubs = _attach_stub_agents(app)

    # 建 v1（active）
    client.post(
        "/api/admin/prompts/classify/versions",
        json={"template": "v1 template", "activate": True},
    )
    client.post("/api/admin/prompts/reload")
    assert stubs["classify"].override == "v1 template"

    # 建 v2（自动 activate，v1 失活）
    client.post(
        "/api/admin/prompts/classify/versions",
        json={"template": "v2 template", "activate": True},
    )
    # 不 reload 之前 agent.override 还是 v1（模拟 lifespan 已经过去）
    assert stubs["classify"].override == "v1 template"

    # reload 后切换到 v2
    resp = client.post("/api/admin/prompts/reload")
    assert resp.status_code == 200
    assert resp.json()["reloaded"]["classify"] == 2
    assert stubs["classify"].override == "v2 template"


def test_reload_clears_override_when_no_active(
    client: TestClient, app: FastAPI
) -> None:
    """active 被全部清掉后 reload → agent.override 还原为 None（代码默认）。"""
    admin = _register(client, "theadmin")
    _promote_to_developer(client, app, admin["user_id"])
    stubs = _attach_stub_agents(app)

    # 建 v1（active）→ reload 注入
    client.post(
        "/api/admin/prompts/classify/versions",
        json={"template": "v1 template", "activate": True},
    )
    client.post("/api/admin/prompts/reload")
    assert stubs["classify"].override == "v1 template"

    # 直接 DB 操作：把 is_active 改成 false（模拟外部干预清掉 active）
    client.portal.call(
        app.state.db_manager.activate_prompt_version, "classify", 999  # 不存在，无副作用
    )
    # 实际清掉需要直接 SQL；这里通过 _session 改 is_active
    async def _deactivate():
        async with app.state.db_manager._session() as session:
            from src.multi_agent_system.models.db import PromptVersionORM
            from sqlalchemy import update
            await session.execute(
                update(PromptVersionORM)
                .where(PromptVersionORM.agent_name == "classify")
                .values(is_active=False)
            )
            await session.commit()
    client.portal.call(_deactivate)

    resp = client.post("/api/admin/prompts/reload")
    assert resp.status_code == 200
    assert "classify" in resp.json()["skipped"]
    # override 被显式清回 None
    assert stubs["classify"].override is None


def test_reload_unauthenticated_returns_401(client: TestClient) -> None:
    _logout(client)
    resp = client.post("/api/admin/prompts/reload")
    assert resp.status_code == 401


def test_reload_user_role_returns_403(client: TestClient) -> None:
    """user 角色（非 admin/developer）不能 reload。"""
    _register(client, "normaluser")
    resp = client.post("/api/admin/prompts/reload")
    assert resp.status_code == 403


def test_reload_developer_role_returns_200(
    client: TestClient, app: FastAPI
) -> None:
    """developer 也能 reload。"""
    dev = _register(client, "dev1")
    _promote_to_developer(client, app, dev["user_id"])
    client.portal.call(app.state.db_manager.update_user_role, dev["user_id"], "developer")
    _relogin(client, "dev1")

    resp = client.post("/api/admin/prompts/reload")
    assert resp.status_code == 200
