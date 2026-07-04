"""prompt_versions ORM 模型（D-02 Prompt 版本管理）。

5 个 Agent 的 system prompt 多版本管理：
- intent / classify / process / review / coordinator
- 同 (agent_name, version) 唯一
- 同 agent 至多 1 条 is_active=true（用应用层保证，DB 不加 partial index 跨方言兼容）

回滚策略：旧版本不修改，仅切换 is_active；如需"改回旧版再编辑"则复制为新版本。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.multi_agent_system.models.base import Base

__all__ = ["PromptVersionORM", "ALLOWED_AGENT_NAMES"]


# 允许版本管理的 5 个 Agent（与 cached_client._TASK_TYPE_TO_CALL_TYPE 对齐）
ALLOWED_AGENT_NAMES: tuple[str, ...] = (
    "intent",
    "classify",
    "process",
    "review",
    "coordinator",
)


class PromptVersionORM(Base):
    """Prompt 版本表（每行一个 agent 的某个版本快照）。"""

    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint(
            "agent_name",
            "version",
            name="uq_prompt_agent_version",
        ),
        Index("idx_pv_agent_active", "agent_name", "is_active"),
        Index("idx_pv_agent", "agent_name"),
    )

    prompt_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    agent_name: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


_TABLE_COMMENT = "Prompt 版本管理（5 Agent 多版本 + 激活切换；详见 13 号设计文档）"
