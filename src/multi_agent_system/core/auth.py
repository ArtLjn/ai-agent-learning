"""鉴权模块：bcrypt 密码校验 + FastAPI 登录依赖。"""

from typing import Any

import bcrypt
from fastapi import Depends, HTTPException, status
from starlette.requests import HTTPConnection

from src.multi_agent_system.config import Settings

__all__ = [
    "verify_password",
    "hash_password",
    "require_login",
    "get_current_user",
    "get_session_role",
    "get_session_user_id",
    "assert_ticket_access",
]

_PUBLIC_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/auth/me"}


def hash_password(plain: str) -> str:
    """生成 bcrypt 哈希。"""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码与哈希是否匹配。"""
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def is_authenticated(request: HTTPConnection) -> bool:
    """检查当前 session 是否已登录。"""
    user = request.session.get("user") if hasattr(request, "session") else None
    return bool(user)


def get_current_user(request: HTTPConnection) -> dict[str, Any] | None:
    """获取当前登录用户信息，未登录返回 None。"""
    if not hasattr(request, "session"):
        return None
    user = request.session.get("user")
    return user if isinstance(user, dict) else None


async def require_login(request: HTTPConnection) -> dict[str, Any]:
    """FastAPI 依赖：要求已登录，否则 401。

    用法：
        @router.get("/...", dependencies=[Depends(require_login)])
        或 router = APIRouter(dependencies=[Depends(require_login)])
    """
    settings = Settings()
    if not settings.auth_enabled:
        return {"username": "anonymous", "auth_disabled": True}

    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或会话已过期",
            headers={"Location": "/login"},
        )
    await _ensure_session_user_active(request, user)
    return user


# 让 import require_login 的代码也能拿到 Depends 形式
require_login_dep = Depends(require_login)


async def _ensure_session_user_active(
    request: HTTPConnection,
    user: dict[str, Any],
) -> None:
    """校验既有 session 对应账号仍处于 active 状态。

    管理员封禁账号后，用户可能仍持有旧 session。受保护接口在进入业务逻辑
    前统一回查账号状态，避免被禁用账号继续访问工单、资料和管理端接口。
    """
    user_id = user.get("user_id")
    if not user_id:
        return
    app = getattr(request, "app", None)
    db_manager = getattr(getattr(app, "state", None), "db_manager", None)
    if db_manager is None:
        return
    db_user = await db_manager.get_user(user_id)
    if db_user is None or db_user.get("status") == "banned":
        if hasattr(request, "session"):
            request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被禁用",
        )


# ============================================================
# 工单访问隔离（v2.0 用户隔离审计 2026-07-05）
# ============================================================


def get_session_user_id(request: HTTPConnection) -> str | None:
    """从 session 取 user_id；演示模式或未登录返回 None。"""
    user = get_current_user(request)
    if user and user.get("user_id"):
        return user["user_id"]
    return None


def get_session_role(request: HTTPConnection) -> str:
    """从 session 取 role；演示模式（auth_enabled=false）视为 admin 全放行。

    未登录返回 'user'（实际接口会被 require_login 拦截到 401）。
    """
    settings = Settings()
    if not settings.auth_enabled:
        return "admin"
    user = get_current_user(request)
    if not user:
        return "user"
    return user.get("role", "user")


def assert_ticket_access(ticket: dict[str, Any], request: HTTPConnection) -> None:
    """校验当前 session 用户能否访问该工单；不能则抛 403。

    规则：
    - admin/developer：全放行（业务运营 + 调试需要）
    - user：必须 ticket.user_id == session.user_id
    - 演示模式（auth_enabled=false）：全放行
    - ticket.user_id 为空（旧数据）：放行（向后兼容）
    """
    role = get_session_role(request)
    if role in ("admin", "developer"):
        return
    session_user_id = get_session_user_id(request)
    if session_user_id is None:
        # 演示模式或未登录（未登录会被 require_login 提前拦截）
        return
    ticket_user_id = ticket.get("user_id")
    if not ticket_user_id:
        # 旧数据没有 user_id 字段，放行
        return
    if ticket_user_id != session_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问该工单",
        )
