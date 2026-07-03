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

from fastapi import APIRouter, Depends, Request
from sqlalchemy.engine import make_url

from src.multi_agent_system.core.permissions import require_role

__all__ = ["router"]

router = APIRouter(prefix="/admin/config", tags=["admin-config"])


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
        "rag_service": {
            # v2.0 设计：主系统作为 rag-service 的客户端（11 号文档）。
            # 当前未在 Settings 中接入 rag_service_url，统一标记为 not_configured。
            "status": "not_configured",
            "base_url": None,
            "api_key_configured": False,
        },
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
