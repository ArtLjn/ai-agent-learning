"""系统运维管理端：RAG 检索调试接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.multi_agent_system.core.permissions import require_role
from src.multi_agent_system.tools.rag_client import RagChunk, RagServiceUnavailable

__all__ = ["router"]

router = APIRouter(
    prefix="/admin/rag",
    tags=["admin-rag"],
    dependencies=[Depends(require_role("admin", "developer"))],
)


class RagDebugRequest(BaseModel):
    """RAG 调试请求体。"""

    query: str = Field(..., min_length=1, max_length=1000)
    mode: str = Field(default="hybrid")
    top_k: int = Field(default=5, ge=1, le=20)
    rerank_top_k: int = Field(default=3, ge=0, le=20)
    collection: str | None = Field(default=None)
    filters: dict[str, Any] | None = Field(default=None)
    use_hyde: bool = Field(default=False)


def _chunk_to_dict(chunk: RagChunk) -> dict[str, Any]:
    """RagChunk 转成前端调试视图所需结构。"""
    return {
        "id": chunk.id,
        "content": chunk.content,
        "score": chunk.score,
        "chunk_index": chunk.chunk_index,
        "metadata": chunk.metadata,
    }


@router.post("/debug")
async def debug_rag_query(body: RagDebugRequest, request: Request) -> dict[str, Any]:
    """调用 rag-service 执行检索调试，并可选执行 rerank。"""
    rag_client = getattr(request.app.state, "rag_client", None)
    if rag_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="rag-service 客户端未初始化",
        )
    settings = request.app.state.settings
    collection = body.collection or settings.rag_service_collection

    try:
        chunks, debug = await rag_client.retrieve(
            query=body.query,
            mode=body.mode,
            top_k=body.top_k,
            collection=collection,
            filters=body.filters,
            use_hyde=body.use_hyde,
        )
        reranked = (
            await rag_client.rerank(
                query=body.query,
                chunks=chunks,
                top_k=min(body.rerank_top_k, len(chunks)),
            )
            if body.rerank_top_k > 0 and chunks
            else []
        )
    except RagServiceUnavailable as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": "rag_service_unavailable",
                "detail": str(exc),
            },
        )

    return {
        "query": body.query,
        "collection": collection,
        "mode": body.mode,
        "retrieval": {
            "hit_count": len(chunks),
            "debug": debug,
            "results": [_chunk_to_dict(chunk) for chunk in chunks],
        },
        "rerank": {
            "hit_count": len(reranked),
            "results": [_chunk_to_dict(chunk) for chunk in reranked],
        },
        "error": None,
    }
