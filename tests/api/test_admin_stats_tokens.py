"""D-04 Token 成本控制台 admin API 测试。

覆盖（系统级总统计，不按用户分摊）：
- GET /api/admin/stats/tokens    近 N 天汇总（按 model + call_type）
- GET /api/admin/stats/tokens/daily    指定日期明细
- GET /api/admin/stats/tokens/hourly    24 小时分布
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.admin_stats import router as admin_stats_router
from src.multi_agent_system.core.database import DatabaseManager
from tests.conftest import TEST_DATABASE_URL


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
    app.add_middleware(SessionMiddleware, secret_key="test-admin-stats-secret")
    app.include_router(admin_stats_router, prefix="/api")
    return app


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    return _build_app()


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        app.state._portal = c.portal
        yield c


def _accumulate(app: FastAPI, **kwargs: Any) -> None:
    """同步调用 accumulate_token_daily_stats（keyword-only 参数 → 用 partial 包一层）。"""
    import functools

    fn = functools.partial(
        app.state.db_manager.accumulate_token_daily_stats, **kwargs
    )
    app.state._portal.call(fn)


class TestTokenSummary:
    """GET /api/admin/stats/tokens 近 N 天汇总。"""

    def test_empty_summary(self, client: TestClient):
        resp = client.get("/api/admin/stats/tokens")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tokens"] == 0
        assert data["total_requests"] == 0
        assert data["by_model"] == {}

    def test_summary_aggregates_by_model_and_call_type(
        self, client: TestClient, app: FastAPI
    ):
        """按 (model, call_type) 聚合：两次累加合并到同一桶。"""
        today = date.today()
        _accumulate(
            app,
            user_id="u1", date_value=today, model="glm-4.5-air",
            call_type="classify", ticket_id="TK-1",
            prompt_tokens=100, completion_tokens=50,
        )
        _accumulate(
            app,
            user_id="u1", date_value=today, model="glm-4.5-air",
            call_type="classify", ticket_id="TK-1",
            prompt_tokens=80, completion_tokens=40,
        )
        _accumulate(
            app,
            user_id="u1", date_value=today, model="glm-4.6",
            call_type="process", ticket_id="TK-1",
            prompt_tokens=500, completion_tokens=200,
        )

        resp = client.get("/api/admin/stats/tokens?days=7")
        assert resp.status_code == 200
        data = resp.json()
        # 总数：classify(150+120=270) + process(700) = 970
        assert data["total_tokens"] == 270 + 700
        assert data["total_requests"] == 3
        # by_model 桶 2 个
        keys = sorted(data["by_model"].keys())
        assert "glm-4.5-air:classify" in keys
        assert "glm-4.6:process" in keys
        classify = data["by_model"]["glm-4.5-air:classify"]
        assert classify["prompt_tokens"] == 180  # 100+80
        assert classify["completion_tokens"] == 90  # 50+40
        assert classify["total_tokens"] == 270
        assert classify["request_count"] == 2


class TestTokenDaily:
    """GET /api/admin/stats/tokens/daily 指定日期明细。"""

    def test_daily_returns_all_rows_for_date(
        self, client: TestClient, app: FastAPI
    ):
        today = date.today()
        _accumulate(
            app,
            user_id="u1", date_value=today, model="glm-4.5-air",
            call_type="classify", ticket_id="TK-1",
            prompt_tokens=100, completion_tokens=50,
        )
        _accumulate(
            app,
            user_id="u1", date_value=today, model="glm-4.6",
            call_type="process", ticket_id="TK-1",
            prompt_tokens=200, completion_tokens=100,
        )

        resp = client.get(f"/api/admin/stats/tokens/daily?date={today.isoformat()}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["date"] == today.isoformat()
        assert len(data["items"]) == 2
        models = {item["model"] for item in data["items"]}
        assert models == {"glm-4.5-air", "glm-4.6"}

    def test_daily_default_to_today(self, client: TestClient):
        """不传 date 默认当天。"""
        resp = client.get("/api/admin/stats/tokens/daily")
        assert resp.status_code == 200
        data = resp.json()
        assert data["date"] == date.today().isoformat()
        assert data["items"] == []


class TestTokenHourly:
    """GET /api/admin/stats/tokens/hourly 24 小时分布。"""

    def test_hourly_empty(self, client: TestClient):
        resp = client.get("/api/admin/stats/tokens/hourly")
        assert resp.status_code == 200
        data = resp.json()
        # 没数据时合计 0
        assert data["total_tokens"] == 0
        assert data["total_requests"] == 0
        assert isinstance(data["items"], list)

    def test_hourly_returns_aggregated_total(
        self, client: TestClient, app: FastAPI
    ):
        """token_daily_stats 不含小时维度，返回当日所有 (model, call_type) 合计。"""
        today = date.today()
        _accumulate(
            app,
            user_id="u1", date_value=today, model="glm-4.5-air",
            call_type="process", ticket_id="TK-1",
            prompt_tokens=300, completion_tokens=100,
        )

        resp = client.get(
            f"/api/admin/stats/tokens/hourly?date={today.isoformat()}"
        )
        assert resp.status_code == 200
        data = resp.json()
        # 至少当日 total_tokens 应反映出来
        assert data["total_tokens"] == 400
