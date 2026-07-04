"""D-04 Token 成本控制台 Admin API。

挂在 /api/admin 前缀，整组 require_role("admin", "developer")。

数据源：token_daily_stats 表（C2 已建 + 6 call_type 累加）。
本路由只读：
- GET /admin/stats/tokens                近 N 天汇总（按 model + call_type 分桶）
- GET /admin/stats/tokens/daily          指定日期明细
- GET /admin/stats/tokens/hourly         当日按小时合计（token_daily_stats 无小时维度 → 返回当日聚合）
- GET /admin/stats/quota/{user_id}       用户月/周配额 + 当前用量（本任务只读，不限流）
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.permissions import require_role

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/stats",
    tags=["admin-stats"],
    dependencies=[Depends(require_role("admin", "developer"))],
)


def _get_period_range(period: str) -> tuple[date, date]:
    """计算本月/本周的起止日期（周一为周首）。"""
    today = date.today()
    if period == "month":
        start = today.replace(day=1)
        end = today
    elif period == "week":
        # 周一为周首
        weekday = today.weekday()  # Mon=0
        start = today - timedelta(days=weekday)
        end = today
    else:
        raise ValueError(f"未知 period: {period}")
    return start, end


def _get_quota_limits(settings: Settings) -> tuple[int, int]:
    """读取系统默认月/周配额（config.yaml 的 token_quota.{monthly_limit, weekly_limit}）。"""
    monthly = getattr(settings, "token_quota_monthly_limit", None) or 200_000
    weekly = getattr(settings, "token_quota_weekly_limit", None) or 50_000
    return int(monthly), int(weekly)


@router.get("/tokens")
async def token_summary(
    request: Request,
    user_id: str | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    """近 N 天 Token 用量汇总（按 model + call_type 分桶）。"""
    db_manager = request.app.state.db_manager
    start_date = date.today() - timedelta(days=days - 1)
    rows = await db_manager.list_token_daily_stats(
        user_id=user_id,
        date_from=start_date,
        date_to=date.today(),
        limit=10000,
    )

    by_model: dict[str, dict[str, Any]] = {}
    total_tokens = 0
    total_requests = 0
    for row in rows:
        model = row.get("model", "unknown")
        call_type = row.get("call_type", "process")
        prompt = int(row.get("prompt_tokens") or 0)
        completion = int(row.get("completion_tokens") or 0)
        total = int(row.get("total_tokens") or (prompt + completion))
        count = int(row.get("request_count") or 0)

        key = f"{model}:{call_type}"
        bucket = by_model.setdefault(
            key,
            {
                "model": model,
                "call_type": call_type,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "request_count": 0,
            },
        )
        bucket["prompt_tokens"] += prompt
        bucket["completion_tokens"] += completion
        bucket["total_tokens"] += total
        bucket["request_count"] += count
        total_tokens += total
        total_requests += count

    return {
        "days": days,
        "total_tokens": total_tokens,
        "total_requests": total_requests,
        "by_model": by_model,
    }


@router.get("/tokens/daily")
async def token_daily(
    request: Request,
    user_id: str | None = Query(default=None),
    date_str: str | None = Query(default=None, alias="date"),
) -> dict[str, Any]:
    """指定日期的 Token 用量明细。"""
    db_manager = request.app.state.db_manager
    target_str = date_str or date.today().isoformat()
    try:
        target_date = date.fromisoformat(target_str)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"非法日期格式: {target_str}"
        ) from exc

    rows = await db_manager.list_token_daily_stats(
        user_id=user_id,
        date_from=target_date,
        date_to=target_date,
        limit=10000,
    )

    return {
        "date": target_str,
        "items": [
            {
                "user_id": r.get("user_id"),
                "model": r.get("model"),
                "call_type": r.get("call_type"),
                "ticket_id": r.get("ticket_id"),
                "prompt_tokens": r.get("prompt_tokens", 0),
                "completion_tokens": r.get("completion_tokens", 0),
                "total_tokens": r.get("total_tokens", 0),
                "request_count": r.get("request_count", 0),
                "estimated_cost_cny": float(r.get("estimated_cost_cny") or 0),
            }
            for r in rows
        ],
    }


@router.get("/tokens/hourly")
async def token_hourly(
    request: Request,
    user_id: str | None = Query(default=None),
    date_str: str | None = Query(default=None, alias="date"),
    model: str | None = Query(default=None),
) -> dict[str, Any]:
    """按小时聚合的 LLM Token 用量。

    主系统 token_daily_stats 不含小时维度（详见 12 号文档第 4.3 节），
    本接口退化为按 (model, call_type) 返回当日聚合，items[].hour 字段留空。
    """
    db_manager = request.app.state.db_manager
    target_str = date_str or date.today().isoformat()
    try:
        target_date = date.fromisoformat(target_str)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"非法日期格式: {target_str}"
        ) from exc

    rows = await db_manager.list_token_daily_stats(
        user_id=user_id,
        date_from=target_date,
        date_to=target_date,
        model=model,
        limit=10000,
    )

    items = []
    total_tokens = 0
    total_requests = 0
    for r in rows:
        prompt = int(r.get("prompt_tokens") or 0)
        completion = int(r.get("completion_tokens") or 0)
        total = int(r.get("total_tokens") or (prompt + completion))
        count = int(r.get("request_count") or 0)
        items.append({
            "date": target_str,
            "hour": None,
            "user_id": r.get("user_id"),
            "model": r.get("model"),
            "call_type": r.get("call_type"),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "request_count": count,
        })
        total_tokens += total
        total_requests += count

    return {
        "date": target_str,
        "items": items,
        "hours": [],
        "total_tokens": total_tokens,
        "total_requests": total_requests,
    }


@router.get("/quota/{user_id}")
async def get_user_quota(user_id: str, request: Request) -> dict[str, Any]:
    """用户配额状态（月/周配额 + 当前用量）。本任务只读，不限流。"""
    db_manager = request.app.state.db_manager
    settings = Settings()
    monthly_limit, weekly_limit = _get_quota_limits(settings)

    # 计算 per-user 覆写（如果 users 表有 token_monthly_limit / token_weekly_limit 字段）
    per_user_monthly = None
    per_user_weekly = None
    try:
        per_user_monthly, per_user_weekly = await _lookup_per_user_quota(
            db_manager, user_id
        )
    except Exception as exc:  # noqa: BLE001
        # users 表无配额字段时静默回退到系统默认（详见 12 号文档 3.2 节）
        logger.debug("读取 per-user 配额失败，回退系统默认: %s", exc)

    effective_monthly = per_user_monthly if per_user_monthly else monthly_limit
    effective_weekly = per_user_weekly if per_user_weekly else weekly_limit

    month_start, month_end = _get_period_range("month")
    week_start, week_end = _get_period_range("week")

    monthly_rows = await db_manager.list_token_daily_stats(
        user_id=user_id,
        date_from=month_start,
        date_to=month_end,
        limit=10000,
    )
    weekly_rows = await db_manager.list_token_daily_stats(
        user_id=user_id,
        date_from=week_start,
        date_to=week_end,
        limit=10000,
    )

    monthly_usage = sum(int(r.get("total_tokens") or 0) for r in monthly_rows)
    weekly_usage = sum(int(r.get("total_tokens") or 0) for r in weekly_rows)

    return {
        "user_id": user_id,
        "monthly_limit": effective_monthly,
        "weekly_limit": effective_weekly,
        "monthly_usage": monthly_usage,
        "weekly_usage": weekly_usage,
        "monthly_remaining": max(0, effective_monthly - monthly_usage),
        "weekly_remaining": max(0, effective_weekly - weekly_usage),
        "period_start": {
            "month": month_start.isoformat(),
            "week": week_start.isoformat(),
        },
        "period_end": {
            "month": month_end.isoformat(),
            "week": week_end.isoformat(),
        },
    }


async def _lookup_per_user_quota(
    db_manager: Any, user_id: str
) -> tuple[int | None, int | None]:
    """读 users.token_monthly_limit / token_weekly_limit。

    字段不存在或用户不存在时返回 (None, None)；调用方回退系统默认。
    """
    async with db_manager._session() as session:
        from sqlalchemy import text as _text

        try:
            row = (
                await session.execute(
                    _text(
                        "SELECT token_monthly_limit, token_weekly_limit "
                        "FROM users WHERE user_id = :uid"
                    ),
                    {"uid": user_id},
                )
            ).first()
        except Exception:
            return None, None
        if not row:
            return None, None
        return (
            int(row[0]) if row[0] is not None else None,
            int(row[1]) if row[1] is not None else None,
        )
