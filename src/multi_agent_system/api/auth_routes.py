"""鉴权路由：登录 / 注册 / 退出 / 查当前用户。"""

import json
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.auth import (
    get_current_user,
    hash_password,
    verify_password,
)
from src.multi_agent_system.core.logging import generate_trace_id

__all__ = ["router", "LoginRequest", "RegisterRequest", "public_user"]

router = APIRouter(prefix="/auth", tags=["auth"])


_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


def _parse_preferred_categories(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """脱敏并标准化用户对象。"""
    return {
        "user_id": user.get("user_id"),
        "username": user.get("username"),
        "nickname": user.get("nickname") or user.get("name"),
        "contact": user.get("contact"),
        "vip_level": user.get("vip_level", 0),
        "preferred_categories": _parse_preferred_categories(
            user.get("preferred_categories")
        ),
        "created_at": user.get("created_at"),
        "status": user.get("status", "active"),
        "role": user.get("role", "user"),
    }


class LoginRequest(BaseModel):
    """登录请求体。"""
    username: str
    password: str


class LoginResponse(BaseModel):
    """登录响应。"""
    username: str
    logged_in: bool = True


class RegisterRequest(BaseModel):
    """注册请求体。"""
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=8)
    nickname: str | None = Field(default=None, max_length=32)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    """用户名 + 密码登录，成功后写入 session。

    登录路径：
    1. Settings.auth_username 兜底管理员（视为 admin 角色）
    2. DB-backed 注册用户（按 username 查 → verify_password → 读 DB role）
       admin 在后台改 role 后，被改用户重新 login 即拿到新 role。

    错误：用户名或密码错误返回 401；账户被封禁（status=banned）返回 403。
    """
    settings = Settings()

    # 路径 1：兜底管理员
    if (
        body.username == settings.auth_username
        and verify_password(body.password, settings.auth_password_hash)
    ):
        request.session["user"] = {
            "username": body.username,
            "role": "admin",
        }
        return LoginResponse(username=body.username)

    # 路径 2：DB-backed 用户
    db_manager = request.app.state.db_manager
    user = await db_manager.get_user_by_username(body.username)
    print(f"DEBUG login path2: user={user is None=}, hash={'password_hash' in (user or {})}")
    if user is None or not user.get("password_hash"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    if user.get("status") == "banned":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被封禁",
        )

    request.session["user"] = {
        "user_id": user["user_id"],
        "username": user["username"],
        "nickname": user.get("nickname"),
        "role": user.get("role", "user"),
    }
    return LoginResponse(username=body.username)


@router.post("/register")
async def register(body: RegisterRequest, request: Request) -> JSONResponse:
    """用户自助注册。成功后立即创建 session（自动登录）。

    校验：用户名 3-32 字符 [a-zA-Z0-9_]、密码 >=8。
    错误码：
      - 422 invalid_username / password_too_weak（Pydantic 长度已挡，这里补字符集）
      - 409 username_taken
    """
    if not _USERNAME_RE.match(body.username):
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_username"},
        )

    db_manager = request.app.state.db_manager
    existing = await db_manager.get_user_by_username(body.username)
    if existing is not None:
        return JSONResponse(
            status_code=409,
            content={"error": "username_taken"},
        )

    user_id = f"U-{generate_trace_id()}"
    password_hash = hash_password(body.password)
    try:
        user = await db_manager.create_registered_user({
            "user_id": user_id,
            "username": body.username,
            "password_hash": password_hash,
            "nickname": body.nickname,
        })
    except IntegrityError:
        return JSONResponse(
            status_code=409,
            content={"error": "username_taken"},
        )

    request.session["user"] = {
        "user_id": user["user_id"],
        "username": user["username"],
        "nickname": user.get("nickname"),
        # 注册流程产生的 role 必为 user；提权路径只走 A-04 管理员后台
        "role": user.get("role", "user"),
    }

    safe_user = public_user(user)
    return JSONResponse(
        status_code=201,
        content={"user": safe_user},
    )


@router.post("/logout")
async def logout(request: Request) -> dict[str, Any]:
    """退出登录，清空 session。"""
    request.session.clear()
    return {"logged_out": True}


@router.get("/me")
async def me(request: Request) -> dict[str, Any]:
    """查当前登录状态。无需鉴权依赖，直接读 session。"""
    user = get_current_user(request)
    settings = Settings()
    if user:
        # 演示模式视为 admin（兜底放行所有路由）
        role = user.get("role") or ("admin" if not settings.auth_enabled else "user")
        return {
            "logged_in": True,
            "username": user.get("username"),
            "auth_enabled": settings.auth_enabled,
            "role": role,
        }
    return {
        "logged_in": False,
        "username": None,
        "auth_enabled": settings.auth_enabled,
        "role": None,
    }
