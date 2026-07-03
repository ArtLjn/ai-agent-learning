"""A-07 操作日志审计测试。

覆盖：
- 中间件自动记录 admin PATCH /api/admin/users/{id} 改 role 操作
- 中间件不记录 GET（只捕获写操作）
- 中间件不记录失败响应（仅 2xx）
- detail 过滤密码类字段
- 7 类 action 推断映射表正确
- 列表 200 + 分页正确
- 按 action 筛选
- user 角色 403
"""

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.admin_audit import router as admin_audit_router
from src.multi_agent_system.api.admin_users import router as admin_users_router
from src.multi_agent_system.api.auth_routes import router as auth_router
from src.multi_agent_system.api.user_routes import router as user_router
from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.audit_middleware import (
    ACTION_LABELS,
    AuditMiddleware,
    filter_sensitive_keys,
    parse_audit_request,
)
from src.multi_agent_system.core.auth import require_login
from src.multi_agent_system.core.database import DatabaseManager

from tests.conftest import TEST_DATABASE_URL

_SESSION_SECRET = "test-admin-audit-secret"


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

    # 注册审计中间件：在 Session 之后（更内层）才能读 scope["session"]
    app.add_middleware(
        AuditMiddleware,
        db_manager_getter=lambda: getattr(app.state, "db_manager", None),
    )

    app.include_router(auth_router, prefix="/api")
    app.include_router(
        user_router, prefix="/api", dependencies=[Depends(require_login)]
    )
    app.include_router(admin_users_router, prefix="/api")
    app.include_router(admin_audit_router, prefix="/api")
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
# 纯函数测试：action 推断 / 密码过滤
# ============================================================


def test_action_inference_user_role_change() -> None:
    """PATCH /api/admin/users/{id} + body.role → user_role_change。"""
    result = parse_audit_request(
        "PATCH", "/api/admin/users/U-123", {"role": "developer"}
    )
    assert result == ("user_role_change", "U-123", "user")


def test_action_inference_user_ban() -> None:
    """PATCH /api/admin/users/{id} + status=banned → user_ban。"""
    result = parse_audit_request(
        "PATCH", "/api/admin/users/U-123", {"status": "banned"}
    )
    assert result == ("user_ban", "U-123", "user")


def test_action_inference_user_unban() -> None:
    result = parse_audit_request(
        "PATCH", "/api/admin/users/U-123", {"status": "active"}
    )
    assert result == ("user_unban", "U-123", "user")


def test_action_inference_quota_update() -> None:
    """PATCH /api/admin/users/{id}/quota → quota_update。"""
    result = parse_audit_request(
        "PATCH", "/api/admin/users/U-123/quota", {"weekly_limit": 5000}
    )
    assert result == ("quota_update", "U-123", "user")


def test_action_inference_knowledge_delete() -> None:
    """DELETE /api/admin/knowledge/{id} → knowledge_delete。"""
    result = parse_audit_request("DELETE", "/api/admin/knowledge/doc-1", None)
    assert result == ("knowledge_delete", "doc-1", "knowledge")


def test_action_inference_knowledge_rollback() -> None:
    """POST /api/admin/knowledge/{id}/rollback → knowledge_rollback。"""
    result = parse_audit_request(
        "POST", "/api/admin/knowledge/doc-1/rollback", {"version": 2}
    )
    assert result == ("knowledge_rollback", "doc-1", "knowledge")


def test_action_inference_review_decision() -> None:
    """POST /api/reviews/{id}/decision → review_decision。"""
    result = parse_audit_request(
        "POST", "/api/reviews/R-001/decision", {"decision": "approve"}
    )
    assert result == ("review_decision", "R-001", "review")


def test_action_inference_prompt_activate() -> None:
    """POST /api/admin/prompts/{agent}/activate → prompt_activate。"""
    result = parse_audit_request(
        "POST", "/api/admin/prompts/processor/activate", {"version": "v2"}
    )
    assert result == ("prompt_activate", "processor", "prompt")


def test_action_inference_get_not_captured() -> None:
    """GET 请求不会被捕获（中间件只拦 POST/PATCH/DELETE）。"""
    assert parse_audit_request("GET", "/api/admin/users/U-123", None) is None
    assert parse_audit_request("GET", "/api/admin/audit-logs", None) is None


def test_action_inference_unrelated_path_not_captured() -> None:
    """普通业务路径不被捕获。"""
    assert parse_audit_request("POST", "/api/tickets", None) is None
    assert parse_audit_request("POST", "/api/auth/login", None) is None


def test_filter_sensitive_keys() -> None:
    """密码/token 类字段会被过滤。"""
    keys = ["username", "password", "old_password", "new_password", "role", "token"]
    filtered = filter_sensitive_keys(keys)
    assert set(filtered) == {"password", "old_password", "new_password", "token"}


# ============================================================
# 中间件集成：实际 HTTP 请求触发审计写入
# ============================================================


def test_middleware_records_user_role_change(
    client: TestClient, app: FastAPI
) -> None:
    """admin PATCH 改 role → audit_logs 表出现一条 user_role_change。"""
    # 准备：admin + 普通用户
    target = _register(client, "victim")
    admin = _register(client, "auditor")
    _promote_to_admin(client, app, admin["user_id"])

    # 触发 PATCH
    resp = client.patch(
        f"/api/admin/users/{target['user_id']}",
        json={"role": "developer"},
    )
    assert resp.status_code == 200, resp.text

    # 查审计日志
    resp = client.get("/api/admin/audit-logs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    matching = [
        item
        for item in body["items"]
        if item["action"] == "user_role_change"
        and item["target_id"] == target["user_id"]
    ]
    assert matching, f"未找到 user_role_change 记录: {body['items']}"
    item = matching[0]
    assert item["target_type"] == "user"
    assert item["action_label"] == "用户角色变更"
    # detail 应包含 role 字段
    assert item["detail"] is not None
    assert item["detail"].get("role") == "developer"
    # admin_id 应来自 session
    assert item["admin_id"] is not None


def test_middleware_records_user_ban(client: TestClient, app: FastAPI) -> None:
    """admin PATCH 改 status=banned → user_ban 记录。"""
    target = _register(client, "toban")
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    resp = client.patch(
        f"/api/admin/users/{target['user_id']}",
        json={"status": "banned"},
    )
    assert resp.status_code == 200

    body = client.get("/api/admin/audit-logs").json()
    matching = [
        i for i in body["items"] if i["action"] == "user_ban"
    ]
    assert matching
    assert matching[0]["detail"].get("status") == "banned"


def test_middleware_does_not_record_get(client: TestClient, app: FastAPI) -> None:
    """GET /api/admin/users 不会写 audit_logs。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    # 先清空（如有上次写入）
    before = client.get("/api/admin/audit-logs").json()["total"]
    # 触发 GET
    client.get("/api/admin/users")
    client.get("/api/admin/audit-logs")
    after = client.get("/api/admin/audit-logs").json()["total"]
    # GET 不应增加记录数
    assert after == before, "GET 请求不应写入 audit_logs"


def test_middleware_does_not_record_failed_response(
    client: TestClient, app: FastAPI
) -> None:
    """失败响应（4xx/5xx）不写入 audit_logs（design.md 决策 5）。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    before = client.get("/api/admin/audit-logs").json()["total"]

    # 触发会失败的 PATCH：目标用户不存在 → 404
    resp = client.patch(
        "/api/admin/users/U-nonexistent-xxx",
        json={"role": "developer"},
    )
    assert resp.status_code == 404

    after = client.get("/api/admin/audit-logs").json()["total"]
    assert after == before, "失败响应不应写入 audit_logs"


def test_middleware_filters_password_fields_in_detail(
    client: TestClient, app: FastAPI
) -> None:
    """detail 中不出现 password 字段（即便 body 里有）。

    场景：admin 用 PATCH 改 role 时，如果 body 多传了 password（业务会忽略），
    中间件 detail 也必须过滤掉。
    """
    target = _register(client, "victim2")
    admin = _register(client, "theadmin2")
    _promote_to_admin(client, app, admin["user_id"])

    # 即便业务端会忽略多余字段，中间件 detail 不应泄漏
    resp = client.patch(
        f"/api/admin/users/{target['user_id']}",
        json={"role": "developer", "password": "should_not_leak"},
    )
    assert resp.status_code == 200

    body = client.get("/api/admin/audit-logs").json()
    matching = [
        i
        for i in body["items"]
        if i["action"] == "user_role_change"
        and i["target_id"] == target["user_id"]
    ]
    assert matching
    detail = matching[0]["detail"]
    assert "password" not in detail
    assert "should_not_leak" not in str(detail)


# ============================================================
# 列表查询：分页 / 筛选
# ============================================================


def test_list_pagination(client: TestClient, app: FastAPI) -> None:
    """page_size=2 + 多条记录 → 正确分页。"""
    # 先注册 3 个目标用户（user 角色）
    targets = [_register(client, f"victim_{i}") for i in range(3)]
    # 最后注册 admin 并 promote（避免被后续 register 覆盖 session）
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    # admin 视角逐个改 role，触发 3 次写
    for t in targets:
        resp = client.patch(
            f"/api/admin/users/{t['user_id']}",
            json={"role": "developer"},
        )
        assert resp.status_code == 200

    # 第一页 2 条
    page1 = client.get("/api/admin/audit-logs?page=1&page_size=2").json()
    assert page1["page"] == 1
    assert page1["page_size"] == 2
    assert len(page1["items"]) == 2
    assert page1["total"] >= 3

    # 第二页（应有剩余）
    page2 = client.get("/api/admin/audit-logs?page=2&page_size=2").json()
    assert len(page2["items"]) >= 1


def test_list_filter_by_action(client: TestClient, app: FastAPI) -> None:
    """action=user_role_change 筛选只返回该类型。"""
    target = _register(client, "target1")
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    # 触发 role_change
    client.patch(
        f"/api/admin/users/{target['user_id']}", json={"role": "developer"}
    )
    # 触发 ban
    client.patch(
        f"/api/admin/users/{target['user_id']}", json={"status": "banned"}
    )

    body = client.get("/api/admin/audit-logs?action=user_role_change").json()
    assert body["total"] >= 1
    for item in body["items"]:
        assert item["action"] == "user_role_change"


def test_list_actions_dict_returned(client: TestClient, app: FastAPI) -> None:
    """响应中 actions 字段供前端筛选下拉框。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    body = client.get("/api/admin/audit-logs").json()
    assert "actions" in body
    # 至少包含主要的 7 类 action
    expected = {
        "review_decision",
        "user_role_change",
        "user_ban",
        "user_unban",
        "quota_update",
        "knowledge_delete",
        "knowledge_rollback",
    }
    assert expected.issubset(body["actions"].keys())


# ============================================================
# 鉴权
# ============================================================


def test_list_unauthenticated_returns_401(client: TestClient) -> None:
    _logout(client)
    resp = client.get("/api/admin/audit-logs")
    assert resp.status_code == 401


def test_list_user_role_returns_403(client: TestClient, app: FastAPI) -> None:
    """普通 user 调 /admin/audit-logs → 403。"""
    _register(client, "normaluser")
    resp = client.get("/api/admin/audit-logs")
    assert resp.status_code == 403


def test_invalid_action_filter_returns_422(
    client: TestClient, app: FastAPI
) -> None:
    """action=invalid_xxx → 422 + 返回合法 action 列表。"""
    admin = _register(client, "theadmin")
    _promote_to_admin(client, app, admin["user_id"])

    resp = client.get("/api/admin/audit-logs?action=invalid_xxx")
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid_action"
    assert "valid" in body
