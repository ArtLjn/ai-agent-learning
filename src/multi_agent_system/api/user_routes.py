"""用户自助管理路由：信息维护 + 修改密码（U-03 / U-04）。

挂在 /api 前缀下，整组要求登录（require_login）。
session 内含 user_id 时识别为注册用户；只有 username 时为 Settings 兜底管理员。
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.multi_agent_system.api.auth_routes import public_user
from src.multi_agent_system.core.auth import (
    hash_password,
    require_login,
    verify_password,
)

__all__ = ["router"]

router = APIRouter(prefix="/users", tags=["users"])


_ALLOWED_CATEGORIES = {"technical", "billing", "complaint", "inquiry"}
_EDITABLE_FIELDS = {"nickname", "contact", "preferred_categories"}
_READONLY_FIELDS = {
    "user_id", "username", "vip_level", "created_at",
    "status", "is_admin", "token_monthly_limit", "token_weekly_limit",
}


def _resolve_current_user_id(session_user: dict[str, Any]) -> str | None:
    """从 session 用户字典中取出 user_id（注册流程写入）。"""
    return session_user.get("user_id")


class UpdateMeRequest(BaseModel):
    """PATCH /users/me 请求体。不可改字段出现时被忽略，不报错。"""

    nickname: str | None = Field(default=None, max_length=32)
    contact: str | None = Field(default=None, max_length=128)
    preferred_categories: list[str] | None = None


class ChangePasswordRequest(BaseModel):
    """POST /users/me/password 请求体。"""

    old_password: str
    new_password: str = Field(..., min_length=8)


@router.get("/me")
async def get_me(
    request: Request,
    session_user: dict[str, Any] = Depends(require_login),
) -> dict[str, Any]:
    """返回当前登录用户的完整信息（不含 password_hash）。"""
    user_id = _resolve_current_user_id(session_user)
    if user_id is None:
        # Settings 兜底管理员（无 DB 行），返回 session 内的合成信息
        return public_user({
            "user_id": None,
            "username": session_user.get("username"),
            "nickname": session_user.get("username"),
            "status": "active",
            "vip_level": 0,
            "preferred_categories": [],
        })

    db_manager = request.app.state.db_manager
    user = await db_manager.get_user(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户记录不存在",
        )
    return public_user(user)


@router.patch("/me")
async def update_me(
    body: UpdateMeRequest,
    request: Request,
    session_user: dict[str, Any] = Depends(require_login),
) -> dict[str, Any]:
    """更新 nickname / contact / preferred_categories。

    不可改字段（user_id / username / vip_level / created_at / status /
    token_*_limit / is_admin）请求中出现则忽略——但因 Pydantic 已限定字段，
    客户端塞这些字段在校验阶段会被拒。这里只处理允许字段。
    """
    user_id = _resolve_current_user_id(session_user)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="演示管理员账户不支持修改资料",
        )

    payload = body.model_dump(exclude_unset=True)

    # 校验 preferred_categories 子集
    if "preferred_categories" in payload and payload["preferred_categories"] is not None:
        invalid = set(payload["preferred_categories"]) - _ALLOWED_CATEGORIES
        if invalid:
            return JSONResponse(
                status_code=422,
                content={
                    "error": "invalid_category",
                    "invalid": sorted(invalid),
                },
            )

    db_manager = request.app.state.db_manager
    updated = await db_manager.update_user_profile(user_id, payload)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户记录不存在",
        )
    return public_user(updated)


@router.post("/me/password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    session_user: dict[str, Any] = Depends(require_login),
) -> JSONResponse:
    """修改密码：校验旧密码 -> 写入新哈希 -> 清 session。

    注意：session cookie 是无状态的，无法服务端"撤销所有 session"。
    本接口只清当前请求的 session，前端引导重新登录；其他设备上的旧
    cookie 在自然过期前仍可用——毕设范围接受此简化。
    """
    user_id = _resolve_current_user_id(session_user)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="演示管理员账户不支持修改密码",
        )

    if body.old_password == body.new_password:
        return JSONResponse(
            status_code=422,
            content={"error": "password_same_as_old"},
        )

    db_manager = request.app.state.db_manager
    user = await db_manager.get_user(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户记录不存在",
        )

    stored_hash = user.get("password_hash") or ""
    if not verify_password(body.old_password, stored_hash):
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_credentials"},
        )

    new_hash = hash_password(body.new_password)
    await db_manager.update_user_password(user_id, new_hash)

    # 清当前 session
    request.session.clear()

    return JSONResponse(
        status_code=200,
        content={"success": True, "redirect": "/login"},
    )
