"""工单相关数据模型。"""

from datetime import datetime
from enum import Enum

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "BatchTicketCreate",
    "TicketCategory",
    "TicketCreate",
    "TicketPriority",
    "TicketResponse",
    "TicketStatus",
    "TicketStatusUpdate",
]


class TicketStatus(str, Enum):
    """工单处理状态。"""

    RECEIVED = "received"
    CLASSIFYING = "classifying"
    PROCESSING = "processing"
    REVIEWING = "reviewing"
    PENDING_HUMAN_REVIEW = "pending_human_review"
    WAITING_USER_INPUT = "waiting_user_input"
    COMPLETED = "completed"
    FAILED = "failed"


class TicketCategory(str, Enum):
    """工单分类。"""

    TECHNICAL = "technical"
    BILLING = "billing"
    COMPLAINT = "complaint"
    INQUIRY = "inquiry"


class TicketPriority(str, Enum):
    """工单优先级。"""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class TicketCreate(BaseModel):
    """用户提交的工单。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1)
    service_type: str | None = Field(default=None, max_length=64)
    key_materials: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = None
    customer_id: str | None = None  # backward compat

    @model_validator(mode="after")
    def _validate_content(self) -> "TicketCreate":
        self.content = self.content.strip()
        if not self.content:
            raise ValueError("TICKET_CONTENT_REQUIRED: 问题描述不能为空")
        return self


class TicketResponse(BaseModel):
    """API 返回的工单详情。"""

    ticket_id: str
    content: str
    service_type: str | None = None
    key_materials: dict[str, Any] = Field(default_factory=dict)
    category: TicketCategory | None = None
    priority: TicketPriority | None = None
    processing_result: str | None = None
    references: list[str] = Field(default_factory=list)
    review_score: float | None = None
    retry_count: int = 0
    status: TicketStatus = TicketStatus.RECEIVED
    error: str | None = None
    satisfied: int | bool | None = None
    created_at: datetime = Field(default_factory=datetime.now)


class TicketStatusUpdate(BaseModel):
    """WebSocket 推送的工单状态更新。"""

    ticket_id: str
    status: TicketStatus
    message: str
    timestamp: datetime = Field(default_factory=datetime.now)


class BatchTicketCreate(BaseModel):
    """批量提交工单请求。"""

    tickets: list[TicketCreate] = Field(..., min_length=1, max_length=50)
