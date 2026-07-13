"""SQLAlchemy ORM 表定义。

所有数据库表的 ORM 模型集中在本文件，避免与 Pydantic DTO（ticket.py、review.py、
knowledge.py）的命名冲突。Pydantic 模型用于 API 层数据校验，ORM 模型用于数据库读写，
两者通过 DatabaseManager 内部转换。

索引命名保持与原 SQLite schema 一致，便于运维脚本与测试兼容。
"""

from sqlalchemy import DateTime, Double, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from datetime import datetime

from src.multi_agent_system.models.audit_log import AuditLogORM
from src.multi_agent_system.models.base import Base
from src.multi_agent_system.models.prompt_version import PromptVersionORM
from src.multi_agent_system.models.token_stats import TokenDailyStatsORM

__all__ = [
    "Base",
    "TicketORM",
    "UserORM",
    "CheckpointORM",
    "PatternORM",
    "TraceORM",
    "SpanORM",
    "HumanReviewORM",
    "KnowledgeDocumentORM",
    "KnowledgeVersionORM",
    "TicketMessageORM",
    "AuditLogORM",
    "TokenDailyStatsORM",
    "PromptVersionORM",
]


class TicketORM(Base):
    """工单主表。"""

    __tablename__ = "tickets"

    ticket_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    service_type: Mapped[str | None] = mapped_column(String(64))
    key_materials_json: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64))
    priority: Mapped[str | None] = mapped_column(String(16))
    processing_result: Mapped[str | None] = mapped_column(Text)
    references_json: Mapped[str | None] = mapped_column(Text)
    review_score: Mapped[float | None] = mapped_column(Double)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="received")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str | None] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[str | None] = mapped_column(DateTime)
    satisfied: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    total_duration: Mapped[float] = mapped_column(Double, default=0.0)

    __table_args__ = (
        Index("idx_tickets_user", "user_id"),
        Index("idx_tickets_status", "status"),
        Index("idx_tickets_category", "category"),
        # 替代原 SQLite partial index idx_tickets_pending（MySQL 8 不支持 partial index）
        Index("idx_tickets_status_created", "status", "created_at"),
    )


class UserORM(Base):
    """用户信息表。

    v2.0 扩展字段（U-02/U-03/U-04）：
    - username / password_hash：自助注册产生的凭证（唯一用户名 + bcrypt 哈希）
    - nickname / contact / preferred_categories：可由用户在 Profile 页面维护
    - status：active/banned，配合 A-04 用户管理使用
    - created_at：注册时间
    - role：user / admin / developer（v2.0 设计 3 角色，详见 assets/system-module-architecture-v2-ascii.md）
    旧字段（name / vip_level / preferred_category / avg_satisfaction / total_tickets /
    last_contact）保留，作为扩展上下文使用。
    """

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128))
    vip_level: Mapped[int] = mapped_column(Integer, default=0)
    preferred_category: Mapped[str | None] = mapped_column(String(64))
    avg_satisfaction: Mapped[float | None] = mapped_column(Double)
    total_tickets: Mapped[int] = mapped_column(Integer, default=0)
    last_contact: Mapped[str | None] = mapped_column(DateTime)
    # v2.0 新增字段
    username: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    nickname: Mapped[str | None] = mapped_column(String(32))
    contact: Mapped[str | None] = mapped_column(String(128))
    department: Mapped[str | None] = mapped_column(String(64))
    position: Mapped[str | None] = mapped_column(String(64))
    preferred_categories: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    role: Mapped[str] = mapped_column(String(16), default="user", index=True)


class CheckpointORM(Base):
    """流程中断恢复检查点。"""

    __tablename__ = "checkpoints"

    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(64), unique=True)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str | None] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[str] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("idx_checkpoints_expires", "expires_at"),
    )


class PatternORM(Base):
    """模式匹配知识库。"""

    __tablename__ = "patterns"

    pattern_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    keywords: Mapped[str | None] = mapped_column(Text)
    solution_template: Mapped[str] = mapped_column(Text, nullable=False)
    success_rate: Mapped[float] = mapped_column(Double, default=0.0)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("idx_patterns_category_usage", "category", "usage_count"),
    )


class TraceORM(Base):
    """trace 根表（可观测）。"""

    __tablename__ = "traces"

    trace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    start_time: Mapped[float] = mapped_column(Double, nullable=False)
    end_time: Mapped[float | None] = mapped_column(Double)
    duration: Mapped[float | None] = mapped_column(Double)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_traces_ticket", "ticket_id"),
        Index("idx_traces_status", "status"),
    )


class SpanORM(Base):
    """span 子表（可观测）。"""

    __tablename__ = "spans"

    span_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(64))
    span_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    input_data: Mapped[str | None] = mapped_column(Text)
    output_data: Mapped[str | None] = mapped_column(Text)
    start_time: Mapped[float] = mapped_column(Double, nullable=False)
    end_time: Mapped[float | None] = mapped_column(Double)
    duration: Mapped[float | None] = mapped_column(Double)
    metadata_: Mapped[str | None] = mapped_column("metadata", Text)

    __table_args__ = (
        Index("idx_spans_trace", "trace_id"),
        Index("idx_spans_parent", "parent_span_id"),
        Index("idx_spans_type", "span_type"),
    )


class HumanReviewORM(Base):
    """人工审核工单。"""

    __tablename__ = "human_reviews"

    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_reason: Mapped[str | None] = mapped_column(Text)
    ai_suggestion: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(Text)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    rewritten_result: Mapped[str | None] = mapped_column(Text)
    reviewer_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[str | None] = mapped_column(DateTime)
    decided_at: Mapped[str | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("idx_hr_status", "status"),
        Index("idx_hr_ticket", "ticket_id"),
        Index("idx_hr_trigger", "trigger_type"),
        Index("idx_hr_reviewer", "reviewer_id"),
    )


class TicketMessageORM(Base):
    """工单沟通消息。"""

    __tablename__ = "ticket_messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(24), nullable=False)
    sender_id: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("idx_tm_ticket_created", "ticket_id", "created_at"),
        Index("idx_tm_sender", "sender_type"),
    )


class KnowledgeDocumentORM(Base):
    """服务台知识维护文档主记录。"""

    __tablename__ = "knowledge_documents"

    doc_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(255))
    collection: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str | None] = mapped_column(String(128))
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str | None] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[str | None] = mapped_column(DateTime)
    published_at: Mapped[str | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("idx_kd_status", "status"),
        Index("idx_kd_collection", "collection"),
        Index("idx_kd_category", "category"),
    )


class KnowledgeVersionORM(Base):
    """服务台知识维护版本历史。"""

    __tablename__ = "knowledge_versions"

    version_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str | None] = mapped_column(Text)
    collection: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    rag_doc_id: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str | None] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_kv_doc_version", "doc_id", "version"),
        Index("idx_kv_doc_active", "doc_id", "is_active"),
    )
