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
