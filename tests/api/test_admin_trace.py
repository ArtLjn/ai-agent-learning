"""D-01 Trace 决策树 admin API 测试。

覆盖：
- GET /api/admin/traces/{ticket_id}    完整 trace + spans 树
- GET /api/admin/traces/{ticket_id}/spans/{span_id}    span 详情含 decision 五元组
- GET /api/admin/traces?ticket_id=&status=&page=    列表分页 + 筛选
- require_role('admin','developer') 在演示模式下放行
- 404：trace / span 不存在
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.admin_trace import router as admin_trace_router
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
    app.add_middleware(SessionMiddleware, secret_key="test-admin-trace-secret")
    app.include_router(admin_trace_router, prefix="/api")
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


def _save_span(app: FastAPI, payload: dict[str, Any]) -> None:
    app.state._portal.call(app.state.db_manager.save_span, payload)


def _save_trace(app: FastAPI, payload: dict[str, Any]) -> None:
    app.state._portal.call(app.state.db_manager.save_trace, payload)


class TestAdminTraceByTicket:
    """GET /api/admin/traces/{ticket_id} 完整 trace + span 树。"""

    def test_trace_not_found_returns_404(self, client: TestClient):
        resp = client.get("/api/admin/traces/TK-NONEXIST")
        assert resp.status_code == 404

    def test_trace_with_spans_builds_tree(
        self, client: TestClient, app: FastAPI
    ):
        """parent/child span 正确构建嵌套树。"""
        now = time.time()
        _save_trace(app, {
            "trace_id": "tr-1", "ticket_id": "TK-001",
            "status": "completed", "start_time": now - 1, "end_time": now,
            "duration": 1.0, "total_tokens": 100,
        })
        _save_span(app, {
            "span_id": "sp-root", "trace_id": "tr-1", "parent_span_id": None,
            "span_type": "node", "name": "receive", "status": "ok",
            "start_time": now - 0.9, "end_time": now, "duration": 0.9,
        })
        _save_span(app, {
            "span_id": "sp-classify", "trace_id": "tr-1", "parent_span_id": "sp-root",
            "span_type": "node", "name": "classify", "status": "ok",
            "start_time": now - 0.8, "end_time": now - 0.6, "duration": 0.2,
        })
        _save_span(app, {
            "span_id": "sp-llm", "trace_id": "tr-1", "parent_span_id": "sp-classify",
            "span_type": "llm_call", "name": "chat_completions", "status": "ok",
            "start_time": now - 0.75, "end_time": now - 0.65, "duration": 0.1,
        })

        resp = client.get("/api/admin/traces/TK-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace_id"] == "tr-1"
        assert data["ticket_id"] == "TK-001"
        assert data["total_tokens"] == 100
        roots = data["spans"]
        assert len(roots) == 1
        assert roots[0]["span_id"] == "sp-root"
        assert len(roots[0]["children"]) == 1
        assert roots[0]["children"][0]["span_id"] == "sp-classify"
        assert len(roots[0]["children"][0]["children"]) == 1
        assert roots[0]["children"][0]["children"][0]["span_id"] == "sp-llm"

    def test_trace_includes_decision_highlights(
        self, client: TestClient, app: FastAPI
    ):
        """含 metadata.decision 的 span 应在响应顶层 decisions 列表中暴露。"""
        now = time.time()
        _save_trace(app, {
            "trace_id": "tr-2", "ticket_id": "TK-002",
            "status": "completed", "start_time": now,
        })
        decision_payload = {
            "decision_type": "routing",
            "trigger": {"content_preview": "登录失败"},
            "options": [
                {"value": "technical", "score": 0.82, "reason": "登录关键词"},
                {"value": "billing", "score": 0.15, "reason": "提及订单"},
            ],
            "selection": {"value": "technical", "confidence": 0.82, "reason": "技术信号最强"},
            "execution": {"downstream_node": "route"},
        }
        _save_span(app, {
            "span_id": "sp-decide", "trace_id": "tr-2", "parent_span_id": None,
            "span_type": "node", "name": "classify", "status": "ok",
            "start_time": now, "end_time": now, "duration": 0.1,
            "metadata": json.dumps({"decision": decision_payload}),
        })

        resp = client.get("/api/admin/traces/TK-002")
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision_count"] == 1
        assert data["decisions"][0]["span_id"] == "sp-decide"
        assert data["decisions"][0]["decision_type"] == "routing"
        assert data["decisions"][0]["selection_value"] == "technical"
        assert data["decisions"][0]["confidence"] == pytest.approx(0.82)

    def test_trace_without_decision_metadata_has_empty_state_hint(
        self, client: TestClient, app: FastAPI
    ):
        """无 metadata.decision 时返回显式空态提示，方便运维定位埋点缺失。"""
        now = time.time()
        _save_trace(app, {
            "trace_id": "tr-nodecision", "ticket_id": "TK-NODECISION",
            "status": "completed", "start_time": now,
        })
        _save_span(app, {
            "span_id": "sp-plain", "trace_id": "tr-nodecision",
            "parent_span_id": None, "span_type": "node", "name": "classify",
            "status": "ok", "start_time": now, "duration": 0.1,
            "metadata": json.dumps({"note": "no decision"}),
        })

        resp = client.get("/api/admin/traces/TK-NODECISION")
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision_count"] == 0
        assert data["decision_empty_state"]["reason"] == "missing_decision_metadata"
        assert data["decision_empty_state"]["span_count"] == 1


class TestAdminSpanDetail:
    """GET /api/admin/traces/{ticket_id}/spans/{span_id} span 详情。"""

    def test_span_not_found_returns_404(
        self, client: TestClient, app: FastAPI
    ):
        now = time.time()
        _save_trace(app, {
            "trace_id": "tr-x", "ticket_id": "TK-X",
            "status": "completed", "start_time": now,
        })
        resp = client.get("/api/admin/traces/TK-X/spans/sp-MISSING")
        assert resp.status_code == 404

    def test_span_detail_includes_decision_five_tuple(
        self, client: TestClient, app: FastAPI
    ):
        """span 详情含 metadata.decision 五元组（trigger/options/selection/execution）。"""
        now = time.time()
        _save_trace(app, {
            "trace_id": "tr-d", "ticket_id": "TK-D",
            "status": "completed", "start_time": now,
        })
        decision = {
            "decision_type": "quality_gate",
            "trigger": {"review_score": 0.4, "threshold": 0.7},
            "options": [
                {"value": "pass", "score": 0.4, "reason": "低于阈值"},
                {"value": "retry", "score": 0.6, "reason": "需重试"},
            ],
            "selection": {"value": "retry", "confidence": 0.6, "reason": "未通过质量门"},
            "execution": {"downstream_node": "process"},
        }
        token_usage = {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80}
        _save_span(app, {
            "span_id": "sp-review", "trace_id": "tr-d", "parent_span_id": None,
            "span_type": "node", "name": "review", "status": "ok",
            "start_time": now, "end_time": now, "duration": 0.3,
            "metadata": json.dumps({"decision": decision, "token_usage": token_usage}),
            "input_data": json.dumps({"score": 0.4}),
            "output_data": json.dumps({"action": "retry"}),
        })

        resp = client.get("/api/admin/traces/TK-D/spans/sp-review")
        assert resp.status_code == 200
        data = resp.json()
        assert data["span_id"] == "sp-review"
        assert data["name"] == "review"
        decision = data["metadata"]["decision"]
        assert "trigger" in decision
        assert len(decision["options"]) == 2
        assert decision["selection"]["value"] == "retry"
        assert decision["execution"]["downstream_node"] == "process"
        assert data["metadata"]["token_usage"]["total_tokens"] == 80


class TestAdminTraceList:
    """GET /api/admin/traces 列表 + 分页 + 筛选。"""

    def test_empty_list(self, client: TestClient):
        resp = client.get("/api/admin/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["page_size"] == 20

    def test_filter_by_ticket_id(self, client: TestClient, app: FastAPI):
        now = time.time()
        _save_trace(app, {
            "trace_id": "tr-a", "ticket_id": "TK-A",
            "status": "completed", "start_time": now,
        })
        _save_trace(app, {
            "trace_id": "tr-b", "ticket_id": "TK-B",
            "status": "completed", "start_time": now,
        })

        resp = client.get("/api/admin/traces?ticket_id=TK-A")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["ticket_id"] == "TK-A"

    def test_filter_by_status(self, client: TestClient, app: FastAPI):
        now = time.time()
        _save_trace(app, {
            "trace_id": "tr-s1", "ticket_id": "TK-S1",
            "status": "completed", "start_time": now,
        })
        _save_trace(app, {
            "trace_id": "tr-s2", "ticket_id": "TK-S2",
            "status": "running", "start_time": now,
        })

        resp = client.get("/api/admin/traces?status=running")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "running"

    def test_pagination(self, client: TestClient, app: FastAPI):
        now = time.time()
        for i in range(5):
            _save_trace(app, {
                "trace_id": f"tr-p{i}", "ticket_id": f"TK-P{i}",
                "status": "completed", "start_time": now - i,
            })

        resp = client.get("/api/admin/traces?page=2&page_size=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 2
        assert data["page_size"] == 2
        assert data["total"] == 5
        assert len(data["items"]) == 2
