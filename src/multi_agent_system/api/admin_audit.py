"""A-07 操作日志审计查询接口。

挂在 /api/admin 前缀下，整组要求 admin 角色。

设计：
- 只读查询接口，支持分页 + 多维度筛选
- detail 在 ORM 中以 Text(JSON 字符串) 存储，本层反序列化为 dict 返回前端
- 同时暴露 action 枚举，供前端筛选下拉框使用
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from src.multi_agent_system.core.audit_middleware import ACTION_LABELS
from src.multi_agent_system.core.permissions import require_role

__all__ = ["router"]

router = APIRouter(prefix="/admin/audit-logs", tags=["admin-audit"])


def _parse_detail(raw: Any) -> dict[str, Any] | None:
    """把 ORM Text 字段（JSON 字符串）解析成 dict。解析失败时返回原字符串。"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"_value": parsed}
    except json.JSONDecodeError:
        return {"_raw": raw[:200]}


@router.get("")
async def list_audit_logs(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    admin_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    _admin: dict = Depends(require_role("admin")),
) -> dict[str, Any]:
    """管理员查看操作日志（分页 + 多维筛选）。

    返回结构：
        {
          "items": [{id, admin_id, admin_username, action, action_label,
                     target_type, target_id, detail, ip, created_at}, ...],
          "total": int,
          "page": int,
          "page_size": int,
          "actions": {action: label, ...}  # 供前端筛选下拉框
        }
    """
    if action and action not in ACTION_LABELS:
        return JSONResponse(
            status_code=422,
            content={
                "error": "invalid_action",
                "valid": sorted(ACTION_LABELS.keys()),
            },
        )

    db_manager = request.app.state.db_manager
    items, total = await db_manager.list_audit_logs_paginated(
        page=page,
        page_size=page_size,
        admin_id=admin_id,
        action=action,
        target_type=target_type,
        start_date=start_date,
        end_date=end_date,
    )

    return {
        "items": [
            {
                "id": item["id"],
                "admin_id": item.get("admin_id"),
                "admin_username": item.get("admin_username"),
                "action": item["action"],
                "action_label": ACTION_LABELS.get(item["action"], item["action"]),
                "target_type": item.get("target_type"),
                "target_id": item.get("target_id"),
                "detail": _parse_detail(item.get("detail")),
                "ip": item.get("ip"),
                "created_at": item.get("created_at"),
            }
            for item in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "actions": ACTION_LABELS,
    }
