"""token_daily_stats ORM 模型。

按日聚合 Token 用量，用于 Token 成本控制台（详见 12 号设计文档第 3.1 节）。
唯一约束 (user_id, date, model, call_type)：

- user_id: 可空，系统级调用（如 CoordinatorAgent）记为 NULL
- call_type: 6 枚举 intent/classify/process/review/coordinator/rag
- ticket_id: 关联工单（仅当次 trace），便于按工单回溯成本
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.multi_agent_system.models.base import Base

__all__ = ["TokenDailyStatsORM", "TOKEN_CALL_TYPES"]


# call_type 6 枚举（详见 12 号文档第 7 节）
TOKEN_CALL_TYPES: tuple[str, ...] = (
    "intent",
    "classify",
    "process",
    "review",
    "coordinator",
    "rag",
)


class TokenDailyStatsORM(Base):
    """按日汇总的 Token 用量统计（按 user_id + date + model + call_type 唯一）。"""

    __tablename__ = "token_daily_stats"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "date",
            "model",
            "call_type",
            name="uq_token_daily",
        ),
        Index("idx_tds_user_date", "user_id", "date"),
        Index("idx_tds_date", "date"),
        Index("idx_tds_call_type", "call_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    call_type: Mapped[str] = mapped_column(String(20), nullable=False)
    ticket_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_cny: Mapped[Any] = mapped_column(
        Numeric(10, 6),
        default=0.0,
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )


# 用于 SQL 迁移/文档说明的元字段（不影响 ORM 行为）
_TABLE_COMMENT = "按日聚合的 Token 用量统计（详见 12 号设计文档）"
