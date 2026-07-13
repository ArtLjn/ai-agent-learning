"""系统运维管理端 RAG 调试接口测试。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.admin_config import router as admin_config_router
from src.multi_agent_system.api.admin_rag import router as admin_rag_router
from src.multi_agent_system.api.auth_routes import router as auth_router
from src.multi_agent_system.api.user_routes import router as user_router
from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.auth import require_login
from src.multi_agent_system.core.database import DatabaseManager
from src.multi_agent_system.tools.rag_client import RagChunk, RagServiceUnavailable
from tests.conftest import TEST_DATABASE_URL


class FakeRagClient:
    async def retrieve(
        self,
        query: str,
        mode: str = "hybrid",
        top_k: int = 5,
        collection: str = "default",
        filters: dict[str, Any] | None = None,
        use_hyde: bool = False,
    ) -> tuple[list[RagChunk], dict[str, Any]]:
        return (
            [
                RagChunk(
                    id="doc-1",
                    content=f"{query} 处理手册",
                    score=0.91,
                    chunk_index=0,
                    metadata={"title": "账号权限手册"},
                )
            ],
            {"actual_mode": mode, "query_vector_dim": 1024, "warning": None},
        )

    async def rerank(
        self,
        query: str,
        chunks: list[RagChunk],
        top_k: int = 3,
    ) -> list[RagChunk]:
        return [
            RagChunk(
                id=chunk.id,
                content=chunk.content,
                score=0.97,
                chunk_index=chunk.chunk_index,
                metadata=chunk.metadata,
            )
            for chunk in chunks[:top_k]
        ]


class FailingRagClient:
    async def retrieve(self, *args: Any, **kwargs: Any) -> tuple[list[RagChunk], dict[str, Any]]:
        raise RagServiceUnavailable("rag-service unreachable")


def _build_app(rag_client: Any) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_manager = DatabaseManager(database_url=TEST_DATABASE_URL)
        await db_manager.initialize()
        await db_manager.truncate_all()
        app.state.db_manager = db_manager
        app.state.settings = Settings()
        app.state.rag_client = rag_client
        yield
        await db_manager.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key="test-admin-rag-debug-secret")
    app.include_router(auth_router, prefix="/api")
    app.include_router(user_router, prefix="/api", dependencies=[Depends(require_login)])
    app.include_router(admin_config_router, prefix="/api")
    app.include_router(admin_rag_router, prefix="/api")
    return app


@pytest.fixture
def app():
    return _build_app(FakeRagClient())


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str = "ops") -> dict[str, Any]:
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": "secret123", "nickname": username},
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
    assert updated is not None
    _relogin(client, updated["username"])


def test_rag_debug_returns_retrieval_and_rerank_results(
    client: TestClient, app: FastAPI
) -> None:
    user = _register(client, "opsdev")
    _promote(client, app, user["user_id"], "developer")

    resp = client.post(
        "/api/admin/rag/debug",
        json={"query": "VPN 登录失败", "mode": "hybrid", "top_k": 5, "rerank_top_k": 1},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"] == "VPN 登录失败"
    assert body["retrieval"]["hit_count"] == 1
    assert body["retrieval"]["results"][0]["score"] == 0.91
    assert body["rerank"]["results"][0]["score"] == 0.97
    assert body["error"] is None


def test_rag_debug_exposes_service_errors() -> None:
    app = _build_app(FailingRagClient())
    with TestClient(app) as client:
        user = _register(client, "opsdev2")
        _promote(client, app, user["user_id"], "developer")
        resp = client.post(
            "/api/admin/rag/debug",
            json={"query": "VPN 登录失败"},
        )

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "rag_service_unavailable"
    assert "rag-service unreachable" in body["detail"]
