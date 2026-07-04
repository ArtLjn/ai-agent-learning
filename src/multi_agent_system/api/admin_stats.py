"""管理员/开发者模块：Agent 调用统计路由（D-05）。

挂在 /api/admin 前缀下，要求 admin 或 developer 角色。

GET /api/admin/stats/agents?days=7
返回 5 个 Agent（intent/classify/process/review/coordinator）的：
- call_count: 该 span 调用次数
- avg_duration_ms / max_duration_ms: 平均/最大耗时（毫秒）
- success_rate: status='ok' 的占比
- error_count: status='error' 的次数
- total_tokens: 从 token_daily_stats 聚合（C2 累加结果）
- request_count: token_daily_stats.request_count 之和

数据源：
- spans 表（name=classify/process/review/escalate）—— 4 个 span 名
- token_daily_stats 表 call_type 列（intent/classify/process/review/coordinator）
- intent 没有 span，只取 token_daily_stats 数据；如果 token 数据也无则全 0
"""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from src.multi_agent_system.core.permissions import require_role
from src.multi_agent_system.models.token_stats import TOKEN_CALL_TYPES

__all__ = ["router"]

router = APIRouter(prefix="/admin/stats", tags=["admin-stats"])


# Agent 名 → span name 映射（intent 没有专门 span，留空）
_AGENT_SPAN_NAME: dict[str, str | None] = {
    "intent": None,
    "classify": "classify",
    "process": "process",
    "review": "review",
    "coordinator": "escalate",  # coordinator 主要决策点
}


async def _aggregate_spans(
    db_manager: Any,
    span_name: str | None,
    since: datetime,
) -> dict[str, Any]:
    """按 span name 聚合调用统计。"""
    if span_name is None:
        return {
            "call_count": 0,
            "avg_duration_ms": 0.0,
            "max_duration_ms": 0.0,
            "success_rate": 0.0,
            "error_count": 0,
        }
    from src.multi_agent_system.models.db import SpanORM
    from sqlalchemy import func, select

    async with db_manager._session() as session:
        base_filter = [
            SpanORM.name == span_name,
            SpanORM.span_type == "node",
            SpanORM.start_time >= since.timestamp(),
        ]
        # 总调用数
        total = int((await session.execute(
            select(func.count())
            .select_from(SpanORM)
            .where(*base_filter)
        )).scalar() or 0)

        # 成功 / 失败
        ok_count = int((await session.execute(
            select(func.count())
            .select_from(SpanORM)
            .where(*base_filter, SpanORM.status == "ok")
        )).scalar() or 0)
        err_count = int((await session.execute(
            select(func.count())
            .select_from(SpanORM)
            .where(*base_filter, SpanORM.status.in_(("error", "failed")))
        )).scalar() or 0)

        # 耗时
        duration_row = (await session.execute(
            select(
                func.avg(SpanORM.duration),
                func.max(SpanORM.duration),
            )
            .where(*base_filter, SpanORM.duration.is_not(None))
        )).first()
        avg_dur = float(duration_row[0]) if duration_row and duration_row[0] else 0.0
        max_dur = float(duration_row[1]) if duration_row and duration_row[1] else 0.0

    success_rate = round(ok_count / total, 4) if total > 0 else 0.0
    return {
        "call_count": total,
        "avg_duration_ms": round(avg_dur * 1000, 2),
        "max_duration_ms": round(max_dur * 1000, 2),
        "success_rate": success_rate,
        "error_count": err_count,
    }


async def _aggregate_tokens(
    db_manager: Any,
    call_type: str,
    since: datetime,
) -> dict[str, Any]:
    """从 token_daily_stats 聚合 token 用量（C2 累加结果）。"""
    from src.multi_agent_system.models.db import TokenDailyStatsORM
    from sqlalchemy import func, select

    date_from = since.date()
    async with db_manager._session() as session:
        row = (await session.execute(
            select(
                func.coalesce(func.sum(TokenDailyStatsORM.total_tokens), 0).label("total_tokens"),
                func.coalesce(func.sum(TokenDailyStatsORM.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(TokenDailyStatsORM.completion_tokens), 0).label("completion_tokens"),
                func.coalesce(func.sum(TokenDailyStatsORM.request_count), 0).label("request_count"),
            )
            .where(
                TokenDailyStatsORM.call_type == call_type,
                TokenDailyStatsORM.date >= date_from,
            )
        )).first()
    return {
        "total_tokens": int(row.total_tokens or 0),
        "prompt_tokens": int(row.prompt_tokens or 0),
        "completion_tokens": int(row.completion_tokens or 0),
        "request_count": int(row.request_count or 0),
    }


@router.get("/agents")
async def get_agent_stats(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
    _user: dict = Depends(require_role("admin", "developer")),
) -> dict[str, Any]:
    """聚合 5 个 Agent 的调用统计。

    返回结构：
        {
          "days": 7,
          "since": "2026-06-27T...",
          "agents": [
            {
              "agent_name": "classify",
              "span_name": "classify",
              "call_type": "classify",
              "call_count": int,
              "avg_duration_ms": float,
              "max_duration_ms": float,
              "success_rate": float,
              "error_count": int,
              "total_tokens": int,
              "prompt_tokens": int,
              "completion_tokens": int,
              "request_count": int,
            },
            ...
          ],
        }
    """
    db_manager = request.app.state.db_manager
    since = datetime.utcnow() - timedelta(days=days)

    agents_out: list[dict[str, Any]] = []
    for agent_name in ("intent", "classify", "process", "review", "coordinator"):
        span_name = _AGENT_SPAN_NAME.get(agent_name)
        # call_type 与 agent_name 同名（除 coordinator 外都一致）
        call_type = "coordinator" if agent_name == "coordinator" else agent_name
        if call_type not in TOKEN_CALL_TYPES:
            # intent 在 TOKEN_CALL_TYPES 里有
            call_type = agent_name

        span_stats = await _aggregate_spans(db_manager, span_name, since)
        token_stats = await _aggregate_tokens(db_manager, call_type, since)

        agents_out.append({
            "agent_name": agent_name,
            "span_name": span_name,
            "call_type": call_type,
            **span_stats,
            **token_stats,
        })

    return {
        "days": days,
        "since": since.isoformat(),
        "agents": agents_out,
    }


# ============================================================
# D-04 Token 成本控制台 Admin API（与 D-05 共用一个 router）
#
# 数据源：token_daily_stats 表（C2 已建 + 6 call_type 累加）。
# 本路由只读：
# - GET /admin/stats/tokens                近 N 天汇总（按 model + call_type 分桶）
# - GET /admin/stats/tokens/daily          指定日期明细
# - GET /admin/stats/tokens/hourly         当日按小时合计（token_daily_stats 无小时维度 → 返回当日聚合）
# - GET /admin/stats/quota/{user_id}       用户月/周配额 + 当前用量（本任务只读，不限流）
# ============================================================

import logging
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.permissions import require_role

__all__ = ["router"]

logger = logging.getLogger(__name__)

# D-04 路由复用 D-05 已声明的 router（同 prefix /admin/stats），不重新声明
# dependencies 已在 D-05 router 上挂了 require_role("admin", "developer")


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
