"""执行追踪管理器。

提供 OpenTelemetry 风格的 Trace/Span 模型，
通过 async context manager 实现非侵入式 span 包裹。
使用 contextvars 传递活跃 trace_id 和 span 嵌套关系。
"""

import json
import time
import uuid
from contextvars import ContextVar
from datetime import date
from typing import Any

from loguru import logger

from src.multi_agent_system.core.database import DatabaseManager

__all__ = ["TraceManager", "current_trace_id", "current_span_id"]

# Context variables for active trace/span propagation
current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)
current_span_id: ContextVar[str | None] = ContextVar("current_span_id", default=None)


class SpanContext:
    """Span 上下文管理器，自动记录执行时长和异常。

    用法:
        async with tracer.start_span("classify", "node") as span:
            result = await do_work()
            span.set_output({"category": result["category"]})
    """

    def __init__(
        self,
        trace_manager: "TraceManager",
        span_id: str,
        trace_id: str,
        name: str,
        span_type: str,
        parent_span_id: str | None = None,
        input_data: dict[str, Any] | None = None,
    ) -> None:
        self._manager = trace_manager
        self.span_id = span_id
        self.trace_id = trace_id
        self.name = name
        self.span_type = span_type
        self.parent_span_id = parent_span_id
        self._start_time: float = 0.0
        self._status: str = "ok"
        self._output_data: dict[str, Any] | None = None
        self._metadata: dict[str, Any] | None = None
        self._input_data = input_data
        # 保存外层 span_id，用于 __aexit__ 恢复
        self._prev_span_id: str | None = None

    def set_output(self, data: dict[str, Any]) -> None:
        """设置 span 输出数据。"""
        self._output_data = data

    def set_metadata(self, data: dict[str, Any]) -> None:
        """合并写入 span 元数据（merge 模式，避免覆盖既有字段）。"""
        if self._metadata is None:
            self._metadata = {}
        self._metadata.update(data)

    def set_status(self, status: str) -> None:
        """设置 span 状态（ok/error/fallback）。"""
        self._status = status

    async def __aenter__(self) -> "SpanContext":
        self._start_time = time.time()
        # 保存并替换当前 span_id（用于子 span 嵌套）
        self._prev_span_id = current_span_id.get()
        current_span_id.set(self.span_id)
        # 写入 DB
        await self._manager._db.save_span({
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "span_type": self.span_type,
            "name": self.name,
            "status": self._status,
            "input_data": json.dumps(self._input_data, ensure_ascii=False) if self._input_data else None,
            "start_time": self._start_time,
        })
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        end_time = time.time()
        duration = round(end_time - self._start_time, 6)

        if exc_type is not None:
            self._status = "error"
            self._metadata = self._metadata or {}
            self._metadata["error"] = str(exc_val)

        # 更新 span
        updates: dict[str, Any] = {
            "end_time": end_time,
            "duration": duration,
            "status": self._status,
        }
        if self._output_data is not None:
            updates["output_data"] = json.dumps(self._output_data, ensure_ascii=False)
        if self._metadata is not None:
            updates["metadata"] = json.dumps(self._metadata, ensure_ascii=False)

        await self._manager._db.update_span(self.span_id, updates)

        # 恢复外层 span_id
        current_span_id.set(self._prev_span_id)

        # 更新 trace 的 node/tool 计数
        if self.span_type == "node":
            await self._manager._increment_node_count(self.trace_id)
        elif self.span_type == "tool_call":
            await self._manager._increment_tool_count(self.trace_id)

        # 返回 False 不抑制异常，让异常继续传播
        return False


class TraceManager:
    """执行追踪管理器，管理 trace 生命周期和 span 创建。"""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    async def start_trace(self, ticket_id: str) -> str:
        """创建 trace，返回 trace_id。同时设置 context variable。"""
        trace_id = f"trace-{uuid.uuid4().hex[:12]}"
        start_time = time.time()
        current_trace_id.set(trace_id)
        await self._db.save_trace({
            "trace_id": trace_id,
            "ticket_id": ticket_id,
            "status": "running",
            "start_time": start_time,
            "total_tokens": 0,
            "total_tool_calls": 0,
            "node_count": 0,
        })
        logger.debug(f"[Trace] started {trace_id} for ticket {ticket_id}")
        return trace_id

    async def finish_trace(self, trace_id: str, status: str, error: str | None = None) -> None:
        """完成 trace，计算 duration。"""
        end_time = time.time()
        # 读取 start_time
        trace = await self._db.get_trace_by_ticket(
            await self._get_ticket_id(trace_id)
        )
        duration = 0.0
        if trace and trace.get("start_time"):
            duration = round(end_time - trace["start_time"], 6)

        await self._db.save_trace({
            "trace_id": trace_id,
            "ticket_id": trace["ticket_id"] if trace else "",
            "status": status,
            "start_time": trace["start_time"] if trace else end_time,
            "end_time": end_time,
            "duration": duration,
            "total_tokens": trace.get("total_tokens") or 0 if trace else 0,
            "total_tool_calls": trace.get("total_tool_calls") or 0 if trace else 0,
            "node_count": trace.get("node_count") or 0 if trace else 0,
            "error": error,
        })
        current_trace_id.set(None)
        logger.debug(f"[Trace] finished {trace_id} status={status} duration={duration:.3f}s")

    def start_span(
        self,
        name: str,
        span_type: str,
        parent_span_id: str | None = None,
        input_data: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> SpanContext:
        """创建 span context manager。自动从 context variable 获取 trace_id 和 parent。

        Args:
            name: span 名称
            span_type: span 类型
            parent_span_id: 父 span ID
            input_data: 输入数据
            trace_id: 显式指定 trace_id（用于跨 task 场景，优先于 contextvar）
        """
        # 优先使用显式传入的 trace_id（跨 task 兼容）
        effective_trace_id = trace_id if trace_id is not None else current_trace_id.get()
        if effective_trace_id is None:
            # 无活跃 trace，返回 no-op span
            return _NoOpSpanContext()

        span_id = f"span-{uuid.uuid4().hex[:12]}"
        # 如果未显式指定 parent，使用当前活跃 span
        if parent_span_id is None:
            parent_span_id = current_span_id.get()

        return SpanContext(
            trace_manager=self,
            span_id=span_id,
            trace_id=effective_trace_id,
            name=name,
            span_type=span_type,
            parent_span_id=parent_span_id,
            input_data=input_data,
        )

    async def _increment_node_count(self, trace_id: str) -> None:
        """递增 trace 的 node_count。"""
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE traces SET node_count = node_count + 1 WHERE trace_id = ?",
                (trace_id,),
            )
            await conn.commit()

    async def add_token_usage(self, trace_id: str, delta: int) -> None:
        """累加 trace 的 total_tokens（修复 P0：原本永为 0）。"""
        if delta <= 0:
            return
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE traces SET total_tokens = total_tokens + ? WHERE trace_id = ?",
                (delta, trace_id),
            )
            await conn.commit()

    async def accumulate_token_daily_stats(
        self,
        *,
        trace_id: str,
        model: str,
        call_type: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """按 (user_id, date, model, call_type) 累加 token_daily_stats。

        从 trace 关联的 ticket 反查 user_id（无 ticket 或失败时 user_id=None）。
        用于 Token 成本控制台数据源（详见 12 号设计文档第 4.2 节）。
        """
        if prompt_tokens <= 0 and completion_tokens <= 0:
            return
        try:
            ticket_id = await self._get_ticket_id(trace_id)
            user_id: str | None = None
            if ticket_id:
                ticket = await self._db.get_ticket(ticket_id)
                if ticket:
                    user_id = ticket.get("user_id")
            await self._db.accumulate_token_daily_stats(
                user_id=user_id,
                date_value=date.today(),
                model=model,
                call_type=call_type,
                ticket_id=ticket_id or None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except Exception as e:  # noqa: BLE001
            # token 统计失败不应影响主流程
            logger.warning(f"[Trace] accumulate_token_daily_stats failed: {e}")

    async def _increment_tool_count(self, trace_id: str) -> None:
        """递增 trace 的 total_tool_calls。"""
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE traces SET total_tool_calls = total_tool_calls + 1 WHERE trace_id = ?",
                (trace_id,),
            )
            await conn.commit()

    async def _get_ticket_id(self, trace_id: str) -> str:
        """通过 trace_id 查找 ticket_id。"""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT ticket_id FROM traces WHERE trace_id = ?",
                (trace_id,),
            )
            row = await cursor.fetchone()
            return dict(row)["ticket_id"] if row else ""


class _NoOpSpanContext:
    """无活跃 trace 时的空操作 span，避免 None 检查。"""

    span_id = ""
    trace_id = ""

    def set_output(self, data: dict[str, Any]) -> None:
        pass

    def set_metadata(self, data: dict[str, Any]) -> None:
        pass

    def set_status(self, status: str) -> None:
        pass

    async def __aenter__(self) -> "_NoOpSpanContext":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False
