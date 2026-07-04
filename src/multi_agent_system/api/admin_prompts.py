"""管理员模块：Prompt 版本管理路由（D-02）。

挂在 /api/admin 前缀下，整组要求 admin 角色。
支持 5 个 Agent：intent / classify / process / review / coordinator

接口：
- GET  /api/admin/prompts/{agent_name}/versions: 版本列表
- POST /api/admin/prompts/{agent_name}/versions: 新建版本
- POST /api/admin/prompts/{agent_name}/versions/{version}/activate: 激活
- GET  /api/admin/prompts/{agent_name}/diff?from=v1&to=v2: difflib unified diff
- GET  /api/admin/prompts/{agent_name}/active: 当前激活版本
"""

import difflib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.multi_agent_system.core.permissions import require_role
from src.multi_agent_system.models.prompt_version import ALLOWED_AGENT_NAMES

__all__ = ["router"]

router = APIRouter(
    prefix="/admin/prompts",
    tags=["admin-prompts"],
    dependencies=[Depends(require_role("admin", "developer"))],
)


class CreatePromptVersionRequest(BaseModel):
    """新建 Prompt 版本请求体。"""

    template: str = Field(..., min_length=1, description="Prompt 模板内容")
    note: str | None = Field(default=None, description="版本备注")
    activate: bool = Field(default=True, description="是否同时激活该版本")


def _validate_agent_name(agent_name: str) -> None:
    """agent_name 必须在 5 个白名单内，否则 422。"""
    if agent_name not in ALLOWED_AGENT_NAMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "invalid_agent_name",
                "valid": list(ALLOWED_AGENT_NAMES),
            },
        )


@router.get("/{agent_name}/versions")
async def list_versions(
    agent_name: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """列出某 Agent 的所有 Prompt 版本。"""
    _validate_agent_name(agent_name)
    db_manager = request.app.state.db_manager
    return await db_manager.list_prompt_versions(
        agent_name=agent_name,
        page=page,
        page_size=page_size,
    )


@router.get("/{agent_name}/active")
async def get_active(
    agent_name: str,
    request: Request,
) -> dict[str, Any]:
    """获取某 Agent 当前激活版本（无激活返回 null）。"""
    _validate_agent_name(agent_name)
    db_manager = request.app.state.db_manager
    active = await db_manager.get_active_prompt(agent_name)
    return {"active": active}


@router.post("/{agent_name}/versions", status_code=status.HTTP_201_CREATED)
async def create_version(
    agent_name: str,
    body: CreatePromptVersionRequest,
    request: Request,
) -> dict[str, Any]:
    """新建版本；version 自增。activate=true 时同时切换 active。"""
    _validate_agent_name(agent_name)
    if len(body.template.encode("utf-8")) > 32 * 1024:
        return JSONResponse(
            status_code=422,
            content={"error": "template_too_large", "max_bytes": 32 * 1024},
        )
    db_manager = request.app.state.db_manager
    record = await db_manager.create_prompt_version(
        agent_name=agent_name,
        template=body.template,
        note=body.note,
        activate=body.activate,
    )
    return record


@router.post("/{agent_name}/versions/{version}/activate")
async def activate_version(
    agent_name: str,
    version: int,
    request: Request,
) -> dict[str, Any]:
    """激活指定版本；旧 active 自动置 false。"""
    _validate_agent_name(agent_name)
    db_manager = request.app.state.db_manager
    record = await db_manager.activate_prompt_version(agent_name, version)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"版本不存在: agent={agent_name}, version={version}",
        )
    return record


@router.get("/{agent_name}/diff")
async def diff_versions(
    agent_name: str,
    request: Request,
    from_version: int = Query(..., alias="from", ge=1),
    to_version: int = Query(..., alias="to", ge=1),
) -> dict[str, Any]:
    """difflib unified_diff 对比两个版本。"""
    _validate_agent_name(agent_name)
    db_manager = request.app.state.db_manager
    a = await db_manager.get_prompt_version(agent_name, from_version)
    b = await db_manager.get_prompt_version(agent_name, to_version)
    if a is None or b is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="版本不存在",
        )
    diff_lines = list(
        difflib.unified_diff(
            a["template"].splitlines(keepends=False),
            b["template"].splitlines(keepends=False),
            fromfile=f"v{from_version}",
            tofile=f"v{to_version}",
            lineterm="",
        )
    )
    return {
        "agent_name": agent_name,
        "from_version": from_version,
        "to_version": to_version,
        "diff": "\n".join(diff_lines),
        "has_diff": len(diff_lines) > 0,
    }
