"""A-07 操作日志审计 ORM 表定义。

记录管理员写操作（POST/PATCH/DELETE /api/admin/* 与 POST /api/reviews/{id}/decision），
通过 core/audit_middleware.py 自动写入，不依赖业务路由手动调用。

detail 字段大小限制 4KB（中间件层截断 + 标记 truncated=true）。
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.multi_agent_system.models.base import Base

__all__ = ["AuditLogORM"]


class AuditLogORM(Base):
    """管理员操作日志。

    字段：
    - id: 自增主键
    - admin_id / admin_username: 触发操作的管理员标识（演示模式兜底 admin）
    - action: 操作语义标签（如 user_role_change / knowledge_delete）
    - target_type / target_id: 操作对象（target_id 可空，如批量操作）
    - detail: JSON 字符串，存请求 body 关键字段（密码类字段已过滤）
    - ip: 客户端 IP（X-Forwarded-For 优先）
    - created_at: 操作时间
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[str | None] = mapped_column(String(64))
    admin_username: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_audit_admin_created", "admin_id", "created_at"),
        Index("idx_audit_action_created", "action", "created_at"),
        Index("idx_audit_target", "target_type", "target_id"),
    )
