"""路由级角色权限：require_role(*roles) 返回 FastAPI 依赖。

session 里读 user.role，命中放行，否则 403。
演示模式（auth_enabled=false）由 require_login 已处理（合成 anonymous），
本装饰器在 anonymous 时直接视为 admin 放行，避免演示模式被各种 403 卡死。
"""

from typing import Any

from fastapi import HTTPException, status
from starlette.requests import HTTPConnection

from src.multi_agent_system.config import Settings

__all__ = ["require_role", "ROLE_PERMISSIONS", "get_role_routes"]


# 角色到可见前端路由的映射（前端 Sidebar 与 RequireRole 共用）
# v2.0 设计 3 角色：user / admin / developer（详见 docs/design-spec/
# assets/system-module-architecture-v2-ascii.md）
ROLE_PERMISSIONS: dict[str, list[str]] = {
    "user": ["/", "/tickets", "/tickets/:id", "/profile"],
    "admin": [
        "/", "/tickets", "/tickets/:id", "/profile",
        "/reviews", "/knowledge", "/monitor", "/settings",
        "/admin/users", "/admin/audit-logs",
        "/dev/prompts", "/dev/agent-stats",
    ],
    "developer": [
        "/", "/tickets", "/tickets/:id", "/profile",
        "/reviews", "/monitor",
        "/dev/agent-stats",
    ],
}


_VALID_ROLES = set(ROLE_PERMISSIONS.keys())


def get_role_routes(role: str | None) -> list[str]:
    """根据角色返回可见路由列表（兜底为 user）。"""
    if role not in _VALID_ROLES:
        role = "user"
    return list(ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["user"]))


def _resolve_role(session_user: dict[str, Any], auth_enabled: bool) -> str:
    """从 session 字典解析 role；演示模式兜底 admin。"""
    role = session_user.get("role")
    if role in _VALID_ROLES:
        return role
    # 演示模式视为 admin，全部放行
    if not auth_enabled:
        return "admin"
    return "user"


def require_role(*allowed_roles: str):
    """FastAPI 依赖工厂：要求当前 session.user.role 在 allowed_roles 中。

    用法：
        router = APIRouter(dependencies=[Depends(require_role("admin"))])
        或
        @router.get("/...", dependencies=[Depends(require_role("admin"))])

    演示模式（auth_enabled=false）下视为 admin，永远放行。
    """
    if not allowed_roles:
        raise ValueError("require_role 至少传一个角色")
    invalid = set(allowed_roles) - _VALID_ROLES
    if invalid:
        raise ValueError(f"未知角色: {invalid}，合法值: {sorted(_VALID_ROLES)}")

    async def _dep(request: HTTPConnection) -> dict[str, Any]:
        # 复用 require_login 的逻辑，避免重复实现 session 检查
        from src.multi_agent_system.core.auth import get_current_user
        session_user = get_current_user(request)
        settings = Settings()

        # 演示模式：视为 admin 放行
        if not settings.auth_enabled:
            return {
                "username": "anonymous",
                "auth_disabled": True,
                "role": "admin",
            }

        if not session_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="未登录或会话已过期",
            )

        role = _resolve_role(session_user, settings.auth_enabled)
        if role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：当前角色 {role}，需要 {sorted(allowed_roles)}",
            )
        return {"username": session_user.get("username"), "role": role}

    return _dep
