"""D-05 Agent 调用统计测试。

覆盖：
- /api/admin/stats/agents 返回 5 个 Agent 聚合
- 时间范围过滤（days=7 只返回最近 7 天的 span/token）
- 包含 token 字段（验证 C2 token 累加生效）
- user/admin 角色 403，未登录 401
- developer 角色放行
"""

from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.admin_stats import router as admin_stats_router
from src.multi_agent_system.api.auth_routes import router as auth_router
from src.multi_agent_system.api.user_routes import router as user_router
from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.auth import require_login
from src.multi_agent_system.core.database import DatabaseManager

from tests.conftest import TEST_DATABASE_URL

_SESSION_SECRET = "test-admin-stats-secret"


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
    app.include_router(admin_stats_router, prefix="/api")
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


def _promote(client: TestClient, app: FastAPI, user_id: str, role: str) -> None:
    updated = client.portal.call(app.state.db_manager.update_user_role, user_id, role)
    assert updated is not None and updated["role"] == role
    _relogin(client, updated["username"])


def _logout(client: TestClient) -> None:
    client.post("/api/auth/logout")


def _seed_span(
    db: DatabaseManager,
    *,
    name: str,
    duration_s: float = 0.5,
    status: str = "ok",
    start_offset_s: float = 0.0,
) -> None:
    """插入一个测试 span。"""
    import time
    import uuid

    now = time.time() - start_offset_s
    db_portal = db  # 同步对象，直接传给 portal.call
    # 通过 portal 调用 async save_span
    span_id = f"span-{uuid.uuid4().hex[:12]}"
    trace_id = f"trace-{uuid.uuid4().hex[:12]}"
    # 先建 trace（外键完整性 + start_time 一致）
    db_portal_save_trace = db.save_trace
    db_portal_save_span = db.save_span
    return _await_via_portal(
        db_portal_save_trace,
        {
            "trace_id": trace_id,
            "ticket_id": f"T-{uuid.uuid4().hex[:8]}",
            "status": "completed",
            "start_time": now - 1,
            "end_time": now,
            "duration": 1.0,
        },
    ) or _await_via_portal(
        db_portal_save_span,
        {
            "span_id": span_id,
            "trace_id": trace_id,
            "parent_span_id": None,
            "span_type": "node",
            "name": name,
            "status": status,
            "input_data": "{}",
            "output_data": "{}",
            "start_time": now,
            "end_time": now + duration_s,
            "duration": duration_s,
        },
    )


def _await_via_portal(coro_fn, *args, **kwargs):
    """TestClient.portal 提供同步入口；这里我们用 pytest 的 portal 模式。"""
    # 通过 client.portal 调用；测试里用不到——简化为直接返回 None 占位
    return None


# ============================================================
# 鉴权
# ============================================================


def test_unauthenticated_returns_401(client: TestClient) -> None:
    _logout(client)
    resp = client.get("/api/admin/stats/agents")
    assert resp.status_code == 401


def test_user_role_returns_403(client: TestClient) -> None:
    _register(client, "normaluser")
    resp = client.get("/api/admin/stats/agents")
    assert resp.status_code == 403


def test_admin_role_returns_403(client: TestClient, app: FastAPI) -> None:
    """服务台 admin 不能访问系统运维统计。"""
    admin = _register(client, "admin1")
    _promote(client, app, admin["user_id"], "admin")
    resp = client.get("/api/admin/stats/agents")
    assert resp.status_code == 403


def test_developer_role_returns_200(client: TestClient, app: FastAPI) -> None:
    """developer 也允许访问 D-05。"""
    dev = _register(client, "dev1")
    _promote(client, app, dev["user_id"], "developer")
    resp = client.get("/api/admin/stats/agents")
    assert resp.status_code == 200


# ============================================================
# 数据聚合
# ============================================================


def test_agents_endpoint_returns_5_agents(
    client: TestClient, app: FastAPI
) -> None:
    """返回 5 个 Agent 的统计，字段齐全。"""
    admin = _register(client, "admin1")
    _promote(client, app, admin["user_id"], "developer")

    resp = client.get("/api/admin/stats/agents?days=7")
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 7
    names = [a["agent_name"] for a in body["agents"]]
    assert names == ["intent", "classify", "process", "review", "coordinator"]

    for agent_stats in body["agents"]:
        # 字段齐全
        for key in (
            "call_count", "avg_duration_ms", "max_duration_ms",
            "success_rate", "error_count",
            "total_tokens", "prompt_tokens", "completion_tokens",
            "request_count",
        ):
            assert key in agent_stats, f"missing key {key} in {agent_stats['agent_name']}"


def test_seeded_spans_aggregate_correctly(
    client: TestClient, app: FastAPI
) -> None:
    """种 3 个 classify span（2 ok 1 error）→ call_count=3, error_count=1。"""
    admin = _register(client, "admin1")
    _promote(client, app, admin["user_id"], "developer")
    db: DatabaseManager = app.state.db_manager

    # 用 portal 同步调用 async 方法
    client.portal.call(_seed_3_spans, db)

    resp = client.get("/api/admin/stats/agents?days=7")
    body = resp.json()
    by_name = {a["agent_name"]: a for a in body["agents"]}
    assert by_name["classify"]["call_count"] == 3
    assert by_name["classify"]["error_count"] == 1
    assert 0.0 < by_name["classify"]["success_rate"] < 1.0


async def _seed_3_spans(db: DatabaseManager) -> None:
    """在 portal（async loop）里同步运行：3 个 classify span。"""
    import time
    import uuid

    now = time.time()

    for i, status in enumerate(("ok", "ok", "error")):
        sid = f"span-{uuid.uuid4().hex[:12]}"
        tid = f"trace-{uuid.uuid4().hex[:12]}"
        await db.save_span({
            "span_id": sid,
            "trace_id": tid,
            "parent_span_id": None,
            "span_type": "node",
            "name": "classify",
            "status": status,
            "input_data": "{}",
            "output_data": "{}",
            "start_time": now - i,
            "end_time": now - i + 0.5,
            "duration": 0.5,
        })


def test_seeded_token_daily_stats_aggregate(
    client: TestClient, app: FastAPI
) -> None:
    """种 token_daily_stats call_type=classify 2 行 → total_tokens 累加。"""
    admin = _register(client, "admin1")
    _promote(client, app, admin["user_id"], "developer")
    db: DatabaseManager = app.state.db_manager

    client.portal.call(_seed_token_rows, db)

    resp = client.get("/api/admin/stats/agents?days=7")
    body = resp.json()
    by_name = {a["agent_name"]: a for a in body["agents"]}
    # call_type=classify 的总 token = 100 + 200 = 300
    assert by_name["classify"]["total_tokens"] == 300
    assert by_name["classify"]["request_count"] == 2


async def _seed_token_rows(db: DatabaseManager) -> None:
    """种 2 行 token_daily_stats（call_type=classify）。"""
    from datetime import date

    await db.accumulate_token_daily_stats(
        user_id=None,
        date_value=date.today(),
        model="gpt-test",
        call_type="classify",
        ticket_id=None,
        prompt_tokens=60,
        completion_tokens=40,
    )
    await db.accumulate_token_daily_stats(
        user_id=None,
        date_value=date.today(),
        model="gpt-test",
        call_type="classify",
        ticket_id=None,
        prompt_tokens=120,
        completion_tokens=80,
    )


def test_days_filter_excludes_old_data(client: TestClient, app: FastAPI) -> None:
    """days=1 时 8 天前的 token 不计入。"""
    admin = _register(client, "admin1")
    _promote(client, app, admin["user_id"], "developer")
    db: DatabaseManager = app.state.db_manager

    client.portal.call(_seed_old_token, db)

    resp = client.get("/api/admin/stats/agents?days=1")
    body = resp.json()
    by_name = {a["agent_name"]: a for a in body["agents"]}
    assert by_name["classify"]["total_tokens"] == 0


async def _seed_old_token(db: DatabaseManager) -> None:
    """种 1 行 8 天前的 classify token。"""
    from datetime import date, timedelta

    await db.accumulate_token_daily_stats(
        user_id=None,
        date_value=date.today() - timedelta(days=8),
        model="gpt-test",
        call_type="classify",
        ticket_id=None,
        prompt_tokens=999,
        completion_tokens=1,
    )


def test_invalid_days_returns_422(client: TestClient, app: FastAPI) -> None:
    """days=0 / days=100 → 422。"""
    admin = _register(client, "admin1")
    _promote(client, app, admin["user_id"], "developer")

    resp = client.get("/api/admin/stats/agents?days=0")
    assert resp.status_code == 422
    resp = client.get("/api/admin/stats/agents?days=100")
    assert resp.status_code == 422
