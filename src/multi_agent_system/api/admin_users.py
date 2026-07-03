"""管理员模块：用户管理路由（A-04）。

挂在 /api/admin 前缀下，整组要求 admin 角色。
支持：
- GET /api/admin/users: 分页 + 筛选（status / role / keyword）查用户列表
- PATCH /api/admin/users/{user_id}: 改 role 或 status
  · role ∈ {user, admin, developer}
  · status ∈ {active, banned}
  · 不能改自己（防提权 / 降权）
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.multi_agent_system.api.auth_routes import public_user
from src.multi_agent_system.core.auth import require_login
from src.multi_agent_system.core.permissions import require_role

__all__ = ["router"]

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


_VALID_ROLES = {"user", "admin", "developer"}
_VALID_STATUS = {"active", "banned"}


class UpdateUserRequest(BaseModel):
    """PATCH /admin/users/{user_id} 请求体。

    role 与 status 都是可选字段，未传则不修改。
    """

    role: str | None = Field(default=None)
    status: str | None = Field(default=None)


def _resolve_session_user_id(session_user: dict[str, Any]) -> str | None:
    """从 session 字典取 user_id（注册流程写入）；演示模式 / 兜底管理员为 None。"""
    return session_user.get("user_id") if session_user else None


@router.get("")
async def list_users(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    role_filter: str | None = Query(default=None, alias="role"),
    keyword: str | None = Query(default=None),
    _admin: dict = Depends(require_role("admin")),
) -> dict[str, Any]:
    """管理员查看用户列表（分页 + 筛选）。

    返回结构：
        {
          "items": [UserProfile...],
          "total": int,
          "page": int,
          "page_size": int,
        }
    """
    if status_filter is not None and status_filter not in _VALID_STATUS:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_status", "valid": sorted(_VALID_STATUS)},
        )
    if role_filter is not None and role_filter not in _VALID_ROLES:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_role", "valid": sorted(_VALID_ROLES)},
        )

    db_manager = request.app.state.db_manager
    items, total = await db_manager.list_users_paginated(
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        role_filter=role_filter,
        keyword=keyword,
    )
    return {
        "items": [public_user(item) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.patch("/{user_id}")
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    request: Request,
    session_user: dict[str, Any] = Depends(require_login),
    _admin: dict = Depends(require_role("admin")),
) -> dict[str, Any]:
    """管理员修改指定用户的 role 或 status。

    约束：
    - 不能修改自己（防提权 / 降权，演示模式兜底 admin 无 user_id 时跳过此校验）
    - role ∈ {user, admin, developer}
    - status ∈ {active, banned}
    - 目标用户必须存在
    """
    if body.role is None and body.status is None:
        return JSONResponse(
            status_code=422,
            content={"error": "empty_update", "detail": "至少传 role 或 status"},
        )
    if body.role is not None and body.role not in _VALID_ROLES:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_role", "valid": sorted(_VALID_ROLES)},
        )
    if body.status is not None and body.status not in _VALID_STATUS:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_status", "valid": sorted(_VALID_STATUS)},
        )

    current_id = _resolve_session_user_id(session_user)
    if current_id is not None and current_id == user_id:
        return JSONResponse(
            status_code=403,
            content={
                "error": "cannot_modify_self",
                "detail": "不能修改自己的 role 或 status",
            },
        )

    db_manager = request.app.state.db_manager
    target = await db_manager.get_user(user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    if body.role is not None:
        target = await db_manager.update_user_role(user_id, body.role) or target
    if body.status is not None:
        target = await db_manager.update_user_status(user_id, body.status) or target

    return public_user(target)
