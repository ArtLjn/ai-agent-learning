# Execution Trace System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每个工单处理创建完整的执行 Trace，记录所有节点、LLM 调用、工具调用的耗时和输入输出，采用 OpenTelemetry 风格的 Trace/Span 嵌套模型。

**Architecture:** SQLite 两表（traces + spans）存储，`TraceManager` 通过 `async context manager` 提供非侵入式 span 包裹。使用 `contextvars` 传递活跃 trace_id 和 span 嵌套关系。在 LangGraph 节点、ReAct 循环、LLM 调用三层分别集成 span 记录。

**Tech Stack:** Python 3.12, aiosqlite (已有依赖), contextvars (标准库), FastAPI

---

## File Structure

**新建文件：**
- `src/multi_agent_system/core/trace.py` — TraceManager + SpanContext 核心
- `tests/core/test_trace.py` — TraceManager 单元测试
- `tests/api/test_trace_api.py` — Trace API 端点测试

**修改文件：**
- `src/multi_agent_system/core/database.py` — 新增 traces/spans 表 + CRUD 方法
- `src/multi_agent_system/workflow/graph.py` — 节点 span 包裹 + trace 生命周期
- `src/multi_agent_system/agents/processor_react.py` — ReAct 循环 span 嵌套
- `src/multi_agent_system/core/cached_client.py` — LLM 调用 span 记录
- `src/multi_agent_system/api/routes.py` — trace API 端点 + WebSocket span 推送
- `src/multi_agent_system/api/app.py` — TraceManager 初始化
- `src/multi_agent_system/core/__init__.py` — 导出 TraceManager

---

### Task 1: SQLite 表结构 + TraceManager CRUD

**Files:**
- Modify: `src/multi_agent_system/core/database.py:14-65` (新增 DDL)
- Modify: `src/multi_agent_system/core/database.py` (新增 CRUD 方法)
- Test: `tests/core/test_trace.py`

- [ ] **Step 1: 在 `_SCHEMA_SQL` 中追加 traces 和 spans 表 DDL**

在 `database.py` 的 `_SCHEMA_SQL` 常量末尾（L64 索引定义之后、三引号之前）追加：

```python
CREATE TABLE IF NOT EXISTS traces (
    trace_id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    start_time REAL NOT NULL,
    end_time REAL,
    duration REAL,
    total_tokens INTEGER DEFAULT 0,
    total_tool_calls INTEGER DEFAULT 0,
    node_count INTEGER DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,
    span_type TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    input_data TEXT,
    output_data TEXT,
    start_time REAL NOT NULL,
    end_time REAL,
    duration REAL,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_parent ON spans(parent_span_id);
CREATE INDEX IF NOT EXISTS idx_spans_type ON spans(span_type);
CREATE INDEX IF NOT EXISTS idx_traces_ticket ON traces(ticket_id);
CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status);
```

- [ ] **Step 2: 在 DatabaseManager 类中新增 trace CRUD 方法**

在 `save_pattern` 方法之后（约 L293），新增：

```python
    # ============================================================
    # Trace CRUD
    # ============================================================

    async def save_trace(self, trace_data: dict[str, Any]) -> None:
        """保存或更新 trace 记录。"""
        cols = [
            "trace_id", "ticket_id", "status", "start_time",
            "end_time", "duration", "total_tokens", "total_tool_calls",
            "node_count", "error",
        ]
        placeholders = ", ".join(["?"] * len(cols))
        col_str = ", ".join(cols)
        values = [trace_data.get(c) for c in cols]
        async with self.connection() as conn:
            await conn.execute(
                f"INSERT OR REPLACE INTO traces ({col_str}) VALUES ({placeholders})",
                values,
            )
            await conn.commit()

    async def get_trace_by_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        """按 ticket_id 查询 trace。"""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM traces WHERE ticket_id = ? ORDER BY start_time DESC LIMIT 1",
                (ticket_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def list_traces(
        self,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """查询 trace 列表，按 start_time DESC 排序。"""
        query = "SELECT * FROM traces"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY start_time DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with self.connection() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_trace_stats(self, trace_id: str) -> dict[str, Any] | None:
        """获取 trace 的耗时统计。"""
        trace = None
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM traces WHERE trace_id = ?",
                (trace_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            trace = dict(row)

            # 按 span_type 聚合
            cursor = await conn.execute(
                "SELECT span_type, COUNT(*) as count, "
                "AVG(duration) as avg_duration, MAX(duration) as max_duration "
                "FROM spans WHERE trace_id = ? AND duration IS NOT NULL "
                "GROUP BY span_type",
                (trace_id,),
            )
            by_type = {}
            for r in await cursor.fetchall():
                d = dict(r)
                by_type[d["span_type"]] = {
                    "count": d["count"],
                    "avg_duration": round(d["avg_duration"], 4),
                    "max_duration": round(d["max_duration"], 4),
                }
            trace["by_type"] = by_type

            # 最慢的 5 个 span
            cursor = await conn.execute(
                "SELECT name, span_type, duration FROM spans "
                "WHERE trace_id = ? AND duration IS NOT NULL "
                "ORDER BY duration DESC LIMIT 5",
                (trace_id,),
            )
            trace["slowest_spans"] = [dict(r) for r in await cursor.fetchall()]

        return trace
```

- [ ] **Step 3: 在 DatabaseManager 类中新增 span CRUD 方法**

紧接上一步，继续添加：

```python
    async def save_span(self, span_data: dict[str, Any]) -> None:
        """保存 span 记录。"""
        cols = [
            "span_id", "trace_id", "parent_span_id", "span_type",
            "name", "status", "input_data", "output_data",
            "start_time", "end_time", "duration", "metadata",
        ]
        placeholders = ", ".join(["?"] * len(cols))
        col_str = ", ".join(cols)
        values = [span_data.get(c) for c in cols]
        async with self.connection() as conn:
            await conn.execute(
                f"INSERT OR REPLACE INTO spans ({col_str}) VALUES ({placeholders})",
                values,
            )
            await conn.commit()

    async def update_span(self, span_id: str, updates: dict[str, Any]) -> None:
        """更新 span 记录的部分字段。"""
        set_parts = [f"{k} = ?" for k in updates]
        values = list(updates.values()) + [span_id]
        async with self.connection() as conn:
            await conn.execute(
                f"UPDATE spans SET {', '.join(set_parts)} WHERE span_id = ?",
                values,
            )
            await conn.commit()

    async def get_spans_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        """查询 trace 的所有 span，按 start_time 排序。"""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time",
                (trace_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
```

- [ ] **Step 4: 写 trace 数据库 CRUD 测试**

创建 `tests/core/test_trace.py`：

```python
"""Trace 数据库 CRUD 和 TraceManager 单元测试。"""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from src.multi_agent_system.core.database import DatabaseManager
from src.multi_agent_system.core.trace import TraceManager


@pytest.fixture
async def db():
    """创建内存 SQLite 数据库。"""
    manager = DatabaseManager(db_path=":memory:")
    await manager.initialize()
    yield manager
    await manager.close()


class TestTraceDatabase:
    """trace 和 span 表 CRUD 测试。"""

    @pytest.mark.asyncio
    async def test_save_and_get_trace(self, db: DatabaseManager):
        """保存 trace 并按 ticket_id 查询。"""
        trace_data = {
            "trace_id": "tr-001",
            "ticket_id": "TK-001",
            "status": "running",
            "start_time": time.time(),
        }
        await db.save_trace(trace_data)
        result = await db.get_trace_by_ticket("TK-001")
        assert result is not None
        assert result["trace_id"] == "tr-001"
        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_list_traces_with_filter(self, db: DatabaseManager):
        """按 status 过滤 trace 列表。"""
        now = time.time()
        await db.save_trace({"trace_id": "tr-1", "ticket_id": "TK-1", "status": "completed", "start_time": now - 1})
        await db.save_trace({"trace_id": "tr-2", "ticket_id": "TK-2", "status": "running", "start_time": now})
        result = await db.list_traces(status="completed")
        assert len(result) == 1
        assert result[0]["trace_id"] == "tr-1"

    @pytest.mark.asyncio
    async def test_save_and_get_spans(self, db: DatabaseManager):
        """保存 span 并查询。"""
        await db.save_trace({"trace_id": "tr-001", "ticket_id": "TK-001", "status": "running", "start_time": time.time()})
        await db.save_span({
            "span_id": "sp-1", "trace_id": "tr-001", "parent_span_id": None,
            "span_type": "node", "name": "classify", "status": "ok",
            "start_time": time.time(), "duration": 0.1,
        })
        spans = await db.get_spans_by_trace("tr-001")
        assert len(spans) == 1
        assert spans[0]["name"] == "classify"

    @pytest.mark.asyncio
    async def test_update_span(self, db: DatabaseManager):
        """更新 span 的 end_time 和 duration。"""
        await db.save_trace({"trace_id": "tr-001", "ticket_id": "TK-001", "status": "running", "start_time": time.time()})
        await db.save_span({
            "span_id": "sp-1", "trace_id": "tr-001", "parent_span_id": None,
            "span_type": "node", "name": "classify", "status": "ok",
            "start_time": time.time(),
        })
        await db.update_span("sp-1", {"end_time": time.time(), "duration": 0.5, "status": "ok"})
        spans = await db.get_spans_by_trace("tr-001")
        assert spans[0]["duration"] == 0.5

    @pytest.mark.asyncio
    async def test_nested_spans(self, db: DatabaseManager):
        """嵌套 span 的 parent_span_id 关系正确。"""
        await db.save_trace({"trace_id": "tr-001", "ticket_id": "TK-001", "status": "running", "start_time": time.time()})
        await db.save_span({
            "span_id": "sp-1", "trace_id": "tr-001", "parent_span_id": None,
            "span_type": "node", "name": "process", "status": "ok",
            "start_time": time.time(),
        })
        await db.save_span({
            "span_id": "sp-2", "trace_id": "tr-001", "parent_span_id": "sp-1",
            "span_type": "tool_call", "name": "knowledge_search", "status": "ok",
            "start_time": time.time(),
        })
        spans = await db.get_spans_by_trace("tr-001")
        child = [s for s in spans if s["span_id"] == "sp-2"][0]
        assert child["parent_span_id"] == "sp-1"

    @pytest.mark.asyncio
    async def test_get_trace_stats(self, db: DatabaseManager):
        """trace 耗时统计按 span_type 聚合。"""
        now = time.time()
        await db.save_trace({
            "trace_id": "tr-001", "ticket_id": "TK-001", "status": "completed",
            "start_time": now - 1, "end_time": now, "duration": 1.0,
        })
        await db.save_span({
            "span_id": "sp-1", "trace_id": "tr-001", "parent_span_id": None,
            "span_type": "node", "name": "process", "status": "ok",
            "start_time": now - 0.5, "end_time": now, "duration": 0.5,
        })
        await db.save_span({
            "span_id": "sp-2", "trace_id": "tr-001", "parent_span_id": None,
            "span_type": "llm_call", "name": "chat_completions", "status": "ok",
            "start_time": now - 0.3, "end_time": now, "duration": 0.3,
        })
        stats = await db.get_trace_stats("tr-001")
        assert stats is not None
        assert "node" in stats["by_type"]
        assert stats["by_type"]["node"]["count"] == 1
        assert len(stats["slowest_spans"]) == 2
```

- [ ] **Step 5: 运行测试确认 DDL 和 CRUD 正确**

Run: `cd /Users/ljn/Desktop/agent-study/ai-agent-learning && python -m pytest tests/core/test_trace.py -v`
Expected: 6/6 PASSED

- [ ] **Step 6: Commit**

```bash
git add src/multi_agent_system/core/database.py tests/core/test_trace.py
git commit -m "feat: add traces/spans tables and CRUD methods to DatabaseManager"
```

---

### Task 2: TraceManager 核心 + SpanContext

**Files:**
- Create: `src/multi_agent_system/core/trace.py`
- Test: `tests/core/test_trace.py` (追加测试)

- [ ] **Step 1: 创建 `core/trace.py`，实现 TraceManager 和 SpanContext**

```python
"""执行追踪管理器。

提供 OpenTelemetry 风格的 Trace/Span 模型，
通过 async context manager 实现非侵入式 span 包裹。
使用 contextvars 传递活跃 trace_id 和 span 嵌套关系。
"""

import json
import time
import uuid
from contextvars import ContextVar
from typing import Any, AsyncGenerator

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
        """设置 span 元数据。"""
        self._metadata = data

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
            "total_tokens": trace.get("total_tokens", 0) if trace else 0,
            "total_tool_calls": trace.get("total_tool_calls", 0) if trace else 0,
            "node_count": trace.get("node_count", 0) if trace else 0,
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
    ) -> SpanContext:
        """创建 span context manager。自动从 context variable 获取 trace_id 和 parent。"""
        trace_id = current_trace_id.get()
        if trace_id is None:
            # 无活跃 trace，返回 no-op span
            return _NoOpSpanContext()

        span_id = f"span-{uuid.uuid4().hex[:12]}"
        # 如果未显式指定 parent，使用当前活跃 span
        if parent_span_id is None:
            parent_span_id = current_span_id.get()

        return SpanContext(
            trace_manager=self,
            span_id=span_id,
            trace_id=trace_id,
            name=name,
            span_type=span_type,
            parent_span_id=parent_span_id,
            input_data=input_data,
        )

    async def _increment_node_count(self, trace_id: str) -> None:
        """递增 trace 的 node_count。"""
        await self._db.connection()
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE traces SET node_count = node_count + 1 WHERE trace_id = ?",
                (trace_id,),
            )
            await conn.commit()

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
```

- [ ] **Step 2: 追加 TraceManager 集成测试**

在 `tests/core/test_trace.py` 末尾追加：

```python
class TestTraceManager:
    """TraceManager 生命周期和 span 嵌套测试。"""

    @pytest.mark.asyncio
    async def test_start_and_finish_trace(self, db: DatabaseManager):
        """trace 完整生命周期。"""
        from src.multi_agent_system.core.trace import TraceManager, current_trace_id

        manager = TraceManager(db)
        trace_id = await manager.start_trace("TK-001")
        assert trace_id.startswith("trace-")
        assert current_trace_id.get() == trace_id

        await manager.finish_trace(trace_id, "completed")
        assert current_trace_id.get() is None

        trace = await db.get_trace_by_ticket("TK-001")
        assert trace["status"] == "completed"
        assert trace["duration"] is not None
        assert trace["duration"] > 0

    @pytest.mark.asyncio
    async def test_span_context_manager(self, db: DatabaseManager):
        """span 自动记录耗时。"""
        manager = TraceManager(db)
        trace_id = await manager.start_trace("TK-001")

        async with manager.start_span("classify", "node") as span:
            span.set_output({"category": "technical"})

        spans = await db.get_spans_by_trace(trace_id)
        assert len(spans) == 1
        assert spans[0]["name"] == "classify"
        assert spans[0]["duration"] is not None
        assert spans[0]["duration"] > 0

        await manager.finish_trace(trace_id, "completed")

    @pytest.mark.asyncio
    async def test_nested_spans(self, db: DatabaseManager):
        """嵌套 span 的 parent 关系正确。"""
        manager = TraceManager(db)
        trace_id = await manager.start_trace("TK-001")

        async with manager.start_span("process", "node") as parent:
            async with manager.start_span("react_iter", "react_iter") as child:
                child.set_metadata({"iteration": 1})

        spans = await db.get_spans_by_trace(trace_id)
        assert len(spans) == 2
        parent_span = [s for s in spans if s["name"] == "process"][0]
        child_span = [s for s in spans if s["name"] == "react_iter"][0]
        assert child_span["parent_span_id"] == parent_span["span_id"]

        await manager.finish_trace(trace_id, "completed")

    @pytest.mark.asyncio
    async def test_span_captures_exception(self, db: DatabaseManager):
        """span 内异常自动标记 error。"""
        manager = TraceManager(db)
        trace_id = await manager.start_trace("TK-001")

        with pytest.raises(ValueError):
            async with manager.start_span("failing_node", "node") as span:
                raise ValueError("test error")

        spans = await db.get_spans_by_trace(trace_id)
        assert spans[0]["status"] == "error"
        metadata = json.loads(spans[0]["metadata"])
        assert "test error" in metadata["error"]

        await manager.finish_trace(trace_id, "failed", error="test error")

    @pytest.mark.asyncio
    async def test_noop_span_when_no_trace(self, db: DatabaseManager):
        """无活跃 trace 时返回 no-op span，不报错。"""
        from src.multi_agent_system.core.trace import current_trace_id

        manager = TraceManager(db)
        current_trace_id.set(None)

        async with manager.start_span("classify", "node") as span:
            span.set_output({"category": "technical"})

        # 不应写入任何 span
        traces = await db.list_traces()
        assert len(traces) == 0

    @pytest.mark.asyncio
    async def test_node_and_tool_count(self, db: DatabaseManager):
        """node_count 和 total_tool_calls 自动递增。"""
        manager = TraceManager(db)
        trace_id = await manager.start_trace("TK-001")

        async with manager.start_span("classify", "node"):
            pass
        async with manager.start_span("process", "node"):
            async with manager.start_span("knowledge_search", "tool_call"):
                pass

        await manager.finish_trace(trace_id, "completed")

        trace = await db.get_trace_by_ticket("TK-001")
        assert trace["node_count"] == 2
        assert trace["total_tool_calls"] == 1
```

需要在文件顶部添加 `import json`。

- [ ] **Step 3: 运行测试**

Run: `cd /Users/ljn/Desktop/agent-study/ai-agent-learning && python -m pytest tests/core/test_trace.py -v`
Expected: 12/12 PASSED (6 DB + 6 Manager)

- [ ] **Step 4: Commit**

```bash
git add src/multi_agent_system/core/trace.py tests/core/test_trace.py
git commit -m "feat: add TraceManager with SpanContext and context variable propagation"
```

---

### Task 3: LangGraph 节点集成

**Files:**
- Modify: `src/multi_agent_system/workflow/graph.py`

- [ ] **Step 1: 在 graph.py 添加 TraceManager 模块级变量和注入**

在 `graph.py` L86 (`_memory_manager = None`) 之后添加：

```python
# 模块级 TraceManager 引用（由 lifespan 注入）
_trace_manager = None
```

在 `build_ticket_graph` 函数签名中添加 `trace_manager` 参数，并在函数开头注入：

修改函数签名（L385-388）：
```python
def build_ticket_graph(
    settings: Settings | None = None,
    agents: dict | None = None,
    trace_manager: "TraceManager | None" = None,
) -> StateGraph:
```

在 `build_ticket_graph` 的 global 声明行（L403）之后添加：
```python
    global _trace_manager  # noqa: PLW0603
    _trace_manager = trace_manager
```

同时在文件顶部 `TYPE_CHECKING` 块中添加导入：
```python
    from src.multi_agent_system.core.trace import TraceManager
```

- [ ] **Step 2: 在 receive 节点中启动 trace**

修改 `receive` 函数（L89-103），在函数开头添加 trace 启动：

```python
async def receive(state: TicketState) -> dict:
    """初始化工单状态，加载用户长期记忆。"""
    with log_context(agent="receive"):
        # 启动 trace
        if _trace_manager is not None:
            await _trace_manager.start_trace(state["ticket_id"])

        async with (_trace_manager.start_span("receive", "node", input_data={"content": state["content"]}) if _trace_manager else _noop_span()):
            # Load user context if user_id present
            user_context = {}
            if _memory_manager and state.get("user_id"):
                user_context = await _memory_manager.load_user_context(state["user_id"])
                await _memory_manager.ensure_user(state["user_id"])

            return {
                "status": "received",
                "user_context": user_context,
                "messages": state.get("messages", [])
                + [{"role": "system", "content": f"工单 {state['ticket_id']} 已接收"}],
            }
```

但这样嵌套 context manager + if/else 会很丑。更好的方案：创建一个辅助函数。

在 `_trace_manager = None` 之后添加：

```python
def _noop_span() -> "_NoOpSpanContext":
    """无 TraceManager 时返回空操作 span。"""
    return _NoOpSpanContext()


class _NoOpSpanContext:
    """无活跃 trace 时的空操作 span。"""
    span_id = ""
    trace_id = ""
    def set_output(self, data): pass
    def set_metadata(self, data): pass
    def set_status(self, status): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
```

这样节点代码可以统一写为：
```python
async with _trace_manager.start_span("receive", "node") if _trace_manager else _noop_span() as span:
```

**但实际上这行太长了。** 更实际的方案是给 TraceManager.start_span 加一个安全包装：

在 `_trace_manager = None` 之后改为：

```python
def _span(name: str, span_type: str = "node", **kwargs):
    """获取当前 span context manager，无 TraceManager 时返回 no-op。"""
    if _trace_manager is not None:
        return _trace_manager.start_span(name, span_type, **kwargs)
    return _NoOpSpanContext()
```

- [ ] **Step 3: 为所有节点函数包裹 span**

**receive 节点**（启动 trace + span）：

```python
async def receive(state: TicketState) -> dict:
    """初始化工单状态，加载用户长期记忆。"""
    with log_context(agent="receive"):
        # 启动 trace
        if _trace_manager is not None:
            await _trace_manager.start_trace(state["ticket_id"])

        async with _span("receive", input_data={"content": state["content"]}) as span:
            user_context = {}
            if _memory_manager and state.get("user_id"):
                user_context = await _memory_manager.load_user_context(state["user_id"])
                await _memory_manager.ensure_user(state["user_id"])

            result = {
                "status": "received",
                "user_context": user_context,
                "messages": state.get("messages", [])
                + [{"role": "system", "content": f"工单 {state['ticket_id']} 已接收"}],
            }
            span.set_output({"status": "received"})
            return result
```

**classify 节点**（在 `with log_context(agent="classifier"):` 之后包裹 span）：

在 `content = state["content"]` 之前插入 span 包裹，把整个分类逻辑放在 span 内：

```python
async def classify(state: TicketState) -> dict:
    """分类节点。"""
    with log_context(agent="classifier"):
        async with _span("classify", input_data={"content": state["content"]}) as span:
            content = state["content"]

            if _classifier_agent is not None:
                result = await _classifier_agent.classify(content)
                category = result["category"]
                priority = result["priority"]
                reason = result.get("reason", "")
                span.set_output({"category": category, "priority": priority})
                return {
                    "category": category,
                    "priority": priority,
                    "status": "classifying",
                    "messages": state["messages"]
                    + [{"role": "classifier", "content": f"分类结果: {category}, 优先级: {priority}, 理由: {reason}"}],
                }

            # 占位分类（保持原有逻辑不变）
            for keyword, (category, priority) in _CLASSIFY_RULES.items():
                if keyword in content:
                    span.set_output({"category": category, "priority": priority})
                    return {
                        "category": category,
                        "priority": priority,
                        "status": "classifying",
                        "messages": state["messages"]
                        + [{"role": "classifier", "content": f"分类结果: {category}, 优先级: {priority}"}],
                    }

            span.set_output({"category": "inquiry", "priority": "P3"})
            return {
                "category": TicketCategory.INQUIRY.value,
                "priority": TicketPriority.P3.value,
                "status": "classifying",
                "messages": state["messages"]
                + [{"role": "classifier", "content": f"分类结果: {TicketCategory.INQUIRY.value}, 优先级: {TicketPriority.P3.value}（默认）"}],
            }
```

**route 节点**（空操作节点，不包裹 span）。

**process 节点**（包裹 span，ReAct 内部会在 Task 4 细化）：

```python
async def process(state: TicketState) -> dict:
    """处理节点。"""
    with log_context(agent="processor"):
        async with _span("process", input_data={"category": state.get("category"), "priority": state.get("priority")}) as span:
            if _processor_agent is not None:
                result = await _processor_agent.process(
                    content=state["content"],
                    category=state.get("category", "inquiry"),
                    priority=state.get("priority", "P3"),
                    context=str(state.get("user_context", "")),
                    user_id=state.get("user_id"),
                    memory=_memory_manager,
                )
                span.set_output({"result_length": len(result.get("result", ""))})
                return {
                    "processing_result": result["result"],
                    "status": "processing",
                    "messages": state["messages"]
                    + [{"role": "processor", "content": result["result"]}],
                }

            # 占位处理（保持原有逻辑）
            category = state.get("category", "inquiry")
            result_text = _PLACEHOLDER_RESULTS.get(category, "已记录您的工单，将尽快处理。")
            span.set_output({"result": "placeholder"})
            return {
                "processing_result": result_text,
                "status": "processing",
                "messages": state["messages"]
                + [{"role": "processor", "content": result_text}],
            }
```

**review、auto_reply、escalate、notify 节点**：用同样的模式包裹。每个节点函数用 `_span(node_name)` 包裹，设置 output。

**complete 节点**（完成 trace）：

```python
async def complete(state: TicketState) -> dict:
    """归档节点。"""
    with log_context(agent="complete"):
        async with _span("complete") as span:
            if _trace_manager is not None:
                from src.multi_agent_system.core.trace import current_trace_id
                tid = current_trace_id.get()
                if tid:
                    await _trace_manager.finish_trace(tid, "completed")
            return {"status": "completed"}
```

**handle_failure 节点**（完成 trace 为 failed）：

```python
async def handle_failure(state: TicketState) -> dict:
    """失败处理节点。"""
    with log_context(agent="handle_failure"):
        async with _span("handle_failure") as span:
            error = state.get("error", "未知错误")
            if _trace_manager is not None:
                from src.multi_agent_system.core.trace import current_trace_id
                tid = current_trace_id.get()
                if tid:
                    await _trace_manager.finish_trace(tid, "failed", error=error)
            return {"status": "failed", "error": error}
```

- [ ] **Step 4: 运行全量测试确认无回归**

Run: `cd /Users/ljn/Desktop/agent-study/ai-agent-learning && python -m pytest tests/ -v --tb=short 2>&1 | tail -30`
Expected: 无新增失败

- [ ] **Step 5: Commit**

```bash
git add src/multi_agent_system/workflow/graph.py
git commit -m "feat: wrap LangGraph nodes with trace spans"
```

---

### Task 4: ReAct 循环 Span 嵌套

**Files:**
- Modify: `src/multi_agent_system/agents/processor_react.py`

- [ ] **Step 1: 在 ReAct 循环中为每轮迭代创建 span**

在 `processor_react.py` 中导入 trace 模块：

在文件顶部（现有 import 之后）添加：
```python
from src.multi_agent_system.core.trace import TraceManager, current_trace_id
```

在 `_process_by_react` 方法的 ReAct 循环中（L185 `for iteration in range(...)`），为每轮迭代创建 react_iter span：

```python
        # ReAct loop
        for iteration in range(self._max_iterations):
            logger.info(f"[ReAct] Iteration {iteration + 1}/{self._max_iterations}")

            # 为每轮迭代创建 span（如有活跃 trace）
            iter_span = self._get_react_iter_span(iteration)

            async with iter_span:
                # Trim context before each call
                messages = self._context_manager.trim_messages(messages)

                try:
                    response = await self.client.chat_completions_create(
                        messages=messages,
                        temperature=0.3,
                        task_type=self._task_type,
                    )
                except AuthenticationError as e:
                    raise NonRetryableError(f"API 认证失败: {e}", cause=e)
                except (APIError, APIConnectionError, RateLimitError) as e:
                    raise RetryableError(f"API 调用失败: {e}", cause=e)

                raw = response.choices[0].message.content or ""
                logger.info(f"[ReAct] LLM response: {raw[:200]}...")

                # Check for Final Answer
                if "Final Answer:" in raw:
                    answer = raw.split("Final Answer:")[-1].strip()
                    if memory:
                        memory.add_thought(f"Completed in {iteration + 1} iterations", iteration)
                    iter_span.set_output({"final_answer": True, "iterations": iteration + 1})
                    return {"result": answer, "references": []}

                # Try to parse as direct JSON result
                if raw.strip().startswith("{"):
                    try:
                        parsed = parse_json_response(raw)
                        if "result" in parsed:
                            iter_span.set_output({"json_result": True})
                            return {"result": parsed.get("result", ""), "references": parsed.get("references", [])}
                    except json.JSONDecodeError:
                        pass

                # Parse Thought and Action
                thought = self._extract_thought(raw)
                action = self._extract_action(raw)

                if memory:
                    memory.add_thought(thought or f"Iteration {iteration + 1}", iteration)

                if action:
                    tool_name = action.get("tool", "")
                    params = action.get("params", {})

                    if memory:
                        memory.add_action(tool_name, params, iteration)

                    # 工具调用 span
                    tool_span = self._get_tool_span(tool_name, params)
                    async with tool_span:
                        observation = await self._execute_tool(tool_name, params)
                        tool_span.set_output({"observation_length": len(str(observation))})

                    if memory:
                        memory.add_observation(str(observation), iteration)

                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": f"Observation: {observation}"})
                else:
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": "Observation: 未识别到工具调用，请继续思考或直接给出 Final Answer。"})

                iter_span.set_metadata({"thought": thought, "has_action": action is not None})
```

- [ ] **Step 2: 添加辅助方法获取 span context manager**

在 `ReActProcessorAgent` 类中添加：

```python
    def _get_react_iter_span(self, iteration: int):
        """获取 ReAct 迭代 span context manager。"""
        trace_id = current_trace_id.get()
        if trace_id is None:
            return _NoOpSpan()
        # 需要直接用 TraceManager 的 start_span，因为 context var 会自动获取 parent
        from src.multi_agent_system.core.trace import current_span_id
        # parent 是 process node span（当前活跃 span）
        from src.multi_agent_system.workflow.graph import _trace_manager
        if _trace_manager is None:
            return _NoOpSpan()
        return _trace_manager.start_span(
            f"react_iter_{iteration + 1}",
            "react_iter",
            input_data={"iteration": iteration + 1},
        )

    def _get_tool_span(self, tool_name: str, params: dict):
        """获取工具调用 span context manager。"""
        from src.multi_agent_system.workflow.graph import _trace_manager
        if _trace_manager is None:
            return _NoOpSpan()
        return _trace_manager.start_span(
            tool_name,
            "tool_call",
            input_data={"tool": tool_name, "params": params},
        )
```

在文件底部（class 外部）添加 NoOp 辅助：

```python
class _NoOpSpan:
    """无 trace 时的空操作 span。"""
    span_id = ""
    trace_id = ""
    def set_output(self, data): pass
    def set_metadata(self, data): pass
    def set_status(self, status): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
```

- [ ] **Step 3: 运行测试确认无回归**

Run: `cd /Users/ljn/Desktop/agent-study/ai-agent-learning && python -m pytest tests/ -v --tb=short 2>&1 | tail -30`
Expected: 无新增失败

- [ ] **Step 4: Commit**

```bash
git add src/multi_agent_system/agents/processor_react.py
git commit -m "feat: add ReAct loop iteration and tool call spans"
```

---

### Task 5: LLM 调用 Trace

**Files:**
- Modify: `src/multi_agent_system/core/cached_client.py`

- [ ] **Step 1: 在 chat_completions_create 中添加 trace span**

在 `cached_client.py` 的 `chat_completions_create` 方法中，在 API 调用前后包裹 span：

在文件顶部添加导入：
```python
from src.multi_agent_system.core.trace import current_trace_id
```

在 `chat_completions_create` 内部，模型选择完成后、实际调用前，添加 trace 记录。修改两个调用点（无缓存路径 L77-104 和缓存未命中路径 L121-150）：

**无缓存路径**（L77-104）修改为：
```python
        if llm_cache is None or not cache:
            logger.debug(f"[CachedLLMClient] 跳过缓存，直接调用 {use_model}")
            metrics_collector.record_cache_query(hit=False)

            # LLM trace span
            trace_span = self._get_llm_span(use_model, task_type)
            async with trace_span:
                start = time.time()
                try:
                    result = await self.client.chat.completions.create(
                        model=use_model,
                        messages=messages,
                        temperature=temperature,
                        **kwargs,
                    )
                    duration = time.time() - start
                    metrics_collector.record_llm_call(
                        model=use_model,
                        task_type=task_type or "unknown",
                        duration_seconds=duration,
                    )
                    # 记录 token 数到 trace
                    tokens = getattr(result.usage, "total_tokens", 0) if hasattr(result, "usage") and result.usage else 0
                    trace_span.set_metadata({"model": use_model, "tokens": tokens, "duration": round(duration, 4)})
                    return result
                except Exception as e:
                    duration = time.time() - start
                    metrics_collector.record_llm_call(
                        model=use_model,
                        task_type=task_type or "unknown",
                        duration_seconds=duration,
                        is_error=True,
                        error_type=type(e).__name__,
                    )
                    raise
```

**缓存未命中路径**（L121-150）做类似修改，将 API 调用包裹在 `async with trace_span:` 中，成功后设置 metadata。

- [ ] **Step 2: 添加辅助方法**

在 `CachedLLMClient` 类中添加：

```python
    @staticmethod
    def _get_llm_span(model: str, task_type: str | None):
        """获取 LLM 调用 span。"""
        if current_trace_id.get() is None:
            return _NoOpLLMSpan()
        from src.multi_agent_system.workflow.graph import _trace_manager
        if _trace_manager is None:
            return _NoOpLLMSpan()
        return _trace_manager.start_span(
            "chat_completions",
            "llm_call",
            input_data={"model": model, "task_type": task_type},
        )
```

在文件底部添加：
```python
class _NoOpLLMSpan:
    span_id = ""
    trace_id = ""
    def set_output(self, data): pass
    def set_metadata(self, data): pass
    def set_status(self, status): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
```

- [ ] **Step 3: 运行测试确认无回归**

Run: `cd /Users/ljn/Desktop/agent-study/ai-agent-learning && python -m pytest tests/ --tb=short 2>&1 | tail -30`
Expected: 无新增失败

- [ ] **Step 4: Commit**

```bash
git add src/multi_agent_system/core/cached_client.py
git commit -m "feat: add LLM call trace spans in CachedLLMClient"
```

---

### Task 6: REST API + WebSocket Span 推送

**Files:**
- Modify: `src/multi_agent_system/api/routes.py`
- Test: `tests/api/test_trace_api.py`

- [ ] **Step 1: 添加 trace 查询 API 端点**

在 `routes.py` 的 `GET /api/analytics` 端点之后，添加三个新端点：

```python
@router.get("/tickets/{ticket_id}/trace")
async def get_ticket_trace(ticket_id: str, request: Request) -> dict:
    """获取工单的完整执行 trace。"""
    db_manager = request.app.state.db_manager
    trace = await db_manager.get_trace_by_ticket(ticket_id)
    if trace is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Trace not found"})

    # 获取所有 span 并构建树
    spans = await db_manager.get_spans_by_trace(trace["trace_id"])
    span_tree = _build_span_tree(spans)

    return {
        "trace_id": trace["trace_id"],
        "ticket_id": trace["ticket_id"],
        "status": trace["status"],
        "duration": trace.get("duration"),
        "total_tokens": trace.get("total_tokens", 0),
        "total_tool_calls": trace.get("total_tool_calls", 0),
        "node_count": trace.get("node_count", 0),
        "start_time": trace.get("start_time"),
        "end_time": trace.get("end_time"),
        "spans": span_tree,
    }


@router.get("/traces")
async def list_traces(
    request: Request,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """查询 trace 列表。"""
    limit = min(limit, 100)
    db_manager = request.app.state.db_manager
    traces = await db_manager.list_traces(status=status, limit=limit, offset=offset)
    return {"traces": traces, "count": len(traces)}


@router.get("/traces/{trace_id}/stats")
async def get_trace_stats(trace_id: str, request: Request) -> dict:
    """获取 trace 耗时分析。"""
    db_manager = request.app.state.db_manager
    stats = await db_manager.get_trace_stats(trace_id)
    if stats is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Trace not found"})
    return stats


def _build_span_tree(spans: list[dict]) -> list[dict]:
    """将扁平 span 列表构建为嵌套树结构。"""
    span_map: dict[str, dict] = {}
    roots: list[dict] = []

    for span in spans:
        # 解析 JSON 字段
        for field in ("input_data", "output_data", "metadata"):
            val = span.get(field)
            if isinstance(val, str):
                try:
                    span[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        span["children"] = []
        span_map[span["span_id"]] = span

    for span in spans:
        parent_id = span.get("parent_span_id")
        if parent_id and parent_id in span_map:
            span_map[parent_id]["children"].append(span)
        else:
            roots.append(span)

    return roots
```

需要在 routes.py 顶部添加 `import json`（如果还没有）。

- [ ] **Step 2: 在 _run_workflow 中推送 span_complete 事件**

在 `_run_workflow` 函数的节点完成推送逻辑中（L358-370），追加 span 信息推送：

在现有 `_broadcast_ticket_update` 调用之前，获取刚完成的 span 信息：

```python
                # 推送节点完成事件 + span 信息
                span_data = {
                    "category": db_data.get("category"),
                    "priority": db_data.get("priority"),
                    "review_score": db_data.get("review_score"),
                    "retry_count": db_data.get("retry_count", 0),
                }

                # 获取最近完成的 node span
                if hasattr(app.state, "trace_manager") and app.state.trace_manager:
                    db_mgr = app.state.db_manager
                    trace = await db_mgr.get_trace_by_ticket(ticket_id)
                    if trace:
                        recent_spans = await db_mgr.get_spans_by_trace(trace["trace_id"])
                        node_spans = [s for s in recent_spans if s["span_type"] == "node" and s.get("duration")]
                        if node_spans:
                            last_span = node_spans[-1]
                            span_data["span"] = {
                                "span_id": last_span["span_id"],
                                "span_type": last_span["span_type"],
                                "name": last_span["name"],
                                "duration": last_span["duration"],
                                "status": last_span["status"],
                            }

                await _broadcast_ticket_update(
                    ticket_id=ticket_id,
                    status=status,
                    message=f"{label} 完成",
                    node=node_name,
                    data=span_data,
                )
```

- [ ] **Step 3: 写 API 端点测试**

创建 `tests/api/test_trace_api.py`：

```python
"""Trace API 端点测试。"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.multi_agent_system.core.database import DatabaseManager


@pytest.fixture
async def app_with_trace():
    """创建带 trace 初始化的测试应用。"""
    from src.multi_agent_system.api.app import app

    db = DatabaseManager(db_path=":memory:")
    await db.initialize()

    app.state.db_manager = db
    app.state.db_tool = MagicMock()
    app.state.db_tool.save_ticket = AsyncMock()
    app.state.db_tool.get_ticket = AsyncMock(return_value=None)
    app.state.notification_tool = MagicMock()
    app.state.analytics_tool = MagicMock()
    app.state.knowledge_tool = None
    app.state.memory_manager = None
    app.state.tool_registry = None
    app.state.workflow = MagicMock()
    app.state.trace_manager = None

    yield app
    await db.close()


@pytest.fixture
async def client(app_with_trace):
    """异步测试客户端。"""
    transport = ASGITransport(app=app_with_trace)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestTraceAPI:
    """Trace API 端点测试。"""

    @pytest.mark.asyncio
    async def test_get_trace_not_found(self, client: AsyncClient):
        """查询不存在的 trace 返回 404。"""
        resp = await client.get("/api/tickets/TK-NONEXIST/trace")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Trace not found"

    @pytest.mark.asyncio
    async def test_list_traces_empty(self, client: AsyncClient):
        """空 trace 列表。"""
        resp = await client.get("/api/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_get_trace_stats_not_found(self, client: AsyncClient):
        """查询不存在的 trace stats 返回 404。"""
        resp = await client.get("/api/traces/tr-nonexist/stats")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_trace_with_spans(self, client: AsyncClient, app_with_trace):
        """创建 trace + span 后查询。"""
        db: DatabaseManager = app_with_trace.db_manager
        now = time.time()
        await db.save_trace({
            "trace_id": "tr-test", "ticket_id": "TK-001", "status": "completed",
            "start_time": now - 1, "end_time": now, "duration": 1.0,
        })
        await db.save_span({
            "span_id": "sp-1", "trace_id": "tr-test", "parent_span_id": None,
            "span_type": "node", "name": "classify", "status": "ok",
            "start_time": now - 0.5, "end_time": now, "duration": 0.5,
        })

        resp = await client.get("/api/tickets/TK-001/trace")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace_id"] == "tr-test"
        assert len(data["spans"]) == 1
        assert data["spans"][0]["name"] == "classify"

    @pytest.mark.asyncio
    async def test_list_traces_with_data(self, client: AsyncClient, app_with_trace):
        """有 trace 数据时返回列表。"""
        db: DatabaseManager = app_with_trace.db_manager
        await db.save_trace({
            "trace_id": "tr-1", "ticket_id": "TK-001", "status": "completed",
            "start_time": time.time(),
        })
        resp = await client.get("/api/traces")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_list_traces_status_filter(self, client: AsyncClient, app_with_trace):
        """按 status 过滤 trace 列表。"""
        db: DatabaseManager = app_with_trace.db_manager
        now = time.time()
        await db.save_trace({"trace_id": "tr-1", "ticket_id": "TK-1", "status": "completed", "start_time": now - 1})
        await db.save_trace({"trace_id": "tr-2", "ticket_id": "TK-2", "status": "running", "start_time": now})

        resp = await client.get("/api/traces?status=completed")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
```

- [ ] **Step 4: 运行测试**

Run: `cd /Users/ljn/Desktop/agent-study/ai-agent-learning && python -m pytest tests/api/test_trace_api.py tests/core/test_trace.py -v`
Expected: ALL PASSED

- [ ] **Step 5: Commit**

```bash
git add src/multi_agent_system/api/routes.py tests/api/test_trace_api.py
git commit -m "feat: add trace query API endpoints and WebSocket span push"
```

---

### Task 7: 应用集成 + 全量验证

**Files:**
- Modify: `src/multi_agent_system/api/app.py`
- Modify: `src/multi_agent_system/core/__init__.py`

- [ ] **Step 1: 在 app.py lifespan 中初始化 TraceManager**

在 `app.py` 的 lifespan 函数中，MemoryManager 初始化之后（约 L59），添加：

```python
    # Initialize trace manager
    from src.multi_agent_system.core.trace import TraceManager

    trace_manager = TraceManager(db_manager=db_manager)
```

在 `build_ticket_graph` 调用中传入 `trace_manager`：

修改 L85：
```python
    workflow = build_ticket_graph(settings=settings, agents=agents, trace_manager=trace_manager)
```

在 app.state 存储中添加 trace_manager：
```python
    app.state.trace_manager = trace_manager
```

- [ ] **Step 2: 在 core/__init__.py 中导出 TraceManager**

在 `__init__.py` 的导入部分添加：
```python
from src.multi_agent_system.core.trace import TraceManager
```

在 `__all__` 列表中添加 `"TraceManager"`。

- [ ] **Step 3: 运行全量测试**

Run: `cd /Users/ljn/Desktop/agent-study/ai-agent-learning && python -m pytest tests/ --tb=short 2>&1 | tail -30`
Expected: 无新增失败，所有 trace 相关测试通过

- [ ] **Step 4: Commit**

```bash
git add src/multi_agent_system/api/app.py src/multi_agent_system/core/__init__.py
git commit -m "feat: wire TraceManager into app lifespan and exports"
```

---

### Task 8: 部署验证

- [ ] **Step 1: 推送到 GitHub 并部署到 HomeUbuntu**

```bash
git push origin main
```

在 HomeUbuntu 上：
```bash
cd /home/ljn/ai-agent-learning && git fetch origin && git reset --hard origin/main
pip install -r requirements.txt -q
```

- [ ] **Step 2: 启动服务并验证 trace 端点**

```bash
# 终止旧进程
pkill -f "uvicorn.*multi_agent_system" || true

# 启动
cd /home/ljn/ai-agent-learning && nohup python -m uvicorn src.multi_agent_system.api.app:app --host 0.0.0.0 --port 8001 > /tmp/agent.log 2>&1 &

# 创建测试工单
curl -s -X POST http://172.16.58.68:8001/api/tickets -H "Content-Type: application/json" -d '{"content":"系统报错无法登录"}' | python3 -m json.tool

# 等待处理完成
sleep 10

# 查询 trace
TICKET_ID=$(curl -s http://172.16.58.68:8001/api/tickets?limit=1 | python3 -c "import sys,json; print(json.load(sys.stdin)['tickets'][0]['ticket_id'])")
curl -s http://172.16.58.68:8001/api/tickets/$TICKET_ID/trace | python3 -m json.tool

# 查询 trace 列表
curl -s http://172.16.58.68:8001/api/traces | python3 -m json.tool

# 查询 trace 统计
TRACE_ID=$(curl -s http://172.16.58.68:8001/api/traces | python3 -c "import sys,json; print(json.load(sys.stdin)['traces'][0]['trace_id'])")
curl -s http://172.16.58.68:8001/api/traces/$TRACE_ID/stats | python3 -m json.tool
```

Expected: trace 端点返回完整的 span 树，包含 node/react_iter/llm_call/tool_call 各层。

- [ ] **Step 3: Commit 部署配置（如有变更）**
