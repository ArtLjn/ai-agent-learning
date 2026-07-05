"""A-06 系统配置查看（只读脱敏）。

挂在 /api/admin 前缀下，整组要求 admin 角色。

设计要点：
- 严格只读：不提供任何修改接口（毕设范围）
- 密钥字段（API_KEY / PASSWORD / SECRET）**省略字段不返回**，前端按
  configured=true|false 渲染徽章（design.md 决策 4）
- URL 完整显示，不做脱敏（毕设仅做"密钥全脱敏、URL 完整显示"）
- 6 类配置：LLM / Embedding / Qdrant / rag-service / 数据库 / 鉴权
"""

from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy.engine import make_url

from src.multi_agent_system.core.permissions import require_role

__all__ = ["router"]

router = APIRouter(prefix="/admin/config", tags=["admin-config"])

# rag-service /health 健康检查超时（秒）。短超时避免阻塞 admin/config 响应。
_RAG_HEALTH_TIMEOUT_SECONDS = 2.0


def _mask_database_url(url: str) -> dict[str, Any]:
    """解析 database_url，省略密码字段，返回 driver/host/port/db 等元信息。

    SQLAlchemy make_url 会安全解析失败为 ValueError，避免泄漏异常栈。
    """
    try:
        u = make_url(url)
        return {
            "driver": u.drivername,
            "host": u.host,
            "port": u.port,
            "database": u.database,
            "username_configured": bool(u.username),
            "password_configured": bool(u.password),
        }
    except Exception:
        # 解析失败时只标记 configured 状态，不返回原值
        return {
            "driver": None,
            "host": None,
            "port": None,
            "database": None,
            "username_configured": False,
            "password_configured": False,
            "parse_error": True,
        }


async def _probe_rag_service_health(base_url: str) -> dict[str, Any]:
    """调用 rag-service /health，返回 {status, components, error}。

    超时或异常时 status=unreachable，前端按红色徽章渲染。
    """
    health_url = f"{base_url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=_RAG_HEALTH_TIMEOUT_SECONDS) as client:
            response = await client.get(health_url)
            response.raise_for_status()
            body = response.json()
        return {
            "status": body.get("status", "ok"),
            "components": body.get("components") or {},
            "warning": body.get("warning"),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "status": "unreachable",
            "components": {},
            "warning": None,
            "error": f"{type(e).__name__}: {e}",
        }


async def _build_rag_service_view(settings: Any) -> dict[str, Any]:
    """构造 rag_service 配置视图：base_url + 健康检查 + 客户端配置。

    整合 v2.0 RagClient 接入状态，供 A-06 系统配置查看页展示。
    API Key 仅返回 configured 布尔，不暴露原值（设计决策 4：密钥全脱敏）。
    """
    base_url = settings.rag_service_url
    health = await _probe_rag_service_health(base_url)
    return {
        "base_url": base_url,
        "timeout_seconds": settings.rag_service_timeout_seconds,
        "retry": settings.rag_service_retry,
        "fallback_enabled": settings.rag_service_fallback_enabled,
        "collection": settings.rag_service_collection,
        "status": health["status"],
        "components": health["components"],
        "warning": health["warning"],
        "error": health["error"],
        # rag-service API Key 仅返回是否配置，不返回原值（密钥脱敏）
        "api_key_configured": bool(settings.rag_service_api_key),
    }


@router.get("")
async def get_system_config(
    request: Request,
    _admin: dict = Depends(require_role("admin")),
) -> dict[str, Any]:
    """返回 6 类配置摘要（只读，密钥字段省略）。

    响应结构：
        {
          "llm": {...},
          "embedding": {...},
          "qdrant": {...},
          "rag_service": {...},
          "database": {...},
          "auth": {...},
          "_meta": {"readonly": true, ...}
        }
    """
    settings = request.app.state.settings

    return {
        "llm": {
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
            "temperature": settings.llm_temperature,
            "api_key_configured": bool(settings.llm_api_key),
            "fallback_model": settings.fallback_model,
            "model_routes": dict(settings.model_routes),
        },
        "embedding": {
            "base_url": settings.embedding_base_url,
            "model": settings.embedding_model,
            "dim": settings.embedding_dim,
            "api_key_configured": bool(settings.embedding_api_key),
        },
        "qdrant": {
            "url": settings.qdrant_url,
            "collection": settings.qdrant_collection,
            "top_k": settings.qdrant_top_k,
            "score_threshold": settings.qdrant_score_threshold,
            "batch_size": settings.qdrant_batch_size,
            "api_key_configured": bool(settings.qdrant_api_key),
        },
        "rag_service": await _build_rag_service_view(settings),
        "database": _mask_database_url(settings.database_url),
        "auth": {
            "auth_enabled": settings.auth_enabled,
            "session_cookie": "agentdesk_session",
            "session_max_age_days": 7,
            "password_hash_configured": bool(settings.auth_password_hash),
            "session_secret_configured": bool(settings.auth_session_secret)
            and settings.auth_session_secret
            != "change-me-to-a-random-32-char-string-please",
        },
        "_meta": {
            "readonly": True,
            "version": "v1.0.0",
            "note": "只读视图，配置修改请联系开发人员通过环境变量调整",
        },
    }
