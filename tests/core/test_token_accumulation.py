"""Token 累加修复（v1.1 P0）测试。

验证：
1. db_manager.accumulate_token_daily_stats INSERT 新行
2. 同 (user_id, date, model, call_type) 第二次累加 UPDATE 而非新行
3. user_id=None 视为独立行
4. CachedLLMClient._finalize_llm_span 写入 token_daily_stats
5. traces.total_tokens 修复后非 0
"""

from __future__ import annotations

import time
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.multi_agent_system.core.cache import reset_cache
from src.multi_agent_system.core.cached_client import CachedLLMClient
from src.multi_agent_system.core.database import DatabaseManager
from src.multi_agent_system.core.trace import TraceManager
from tests.conftest import TEST_DATABASE_URL


@pytest.fixture(autouse=True)
def _reset_cache_each_test() -> None:
    """每个测试前后重置全局缓存。"""
    reset_cache()
    yield
    reset_cache()


def _make_mock_response(content: str, prompt: int, completion: int) -> MagicMock:
    """构造模拟的 OpenAI 响应（含 usage）。"""
    usage = MagicMock()
    usage.prompt_tokens = prompt
    usage.completion_tokens = completion
    usage.total_tokens = prompt + completion

    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "stop"

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


class TestTokenDailyStatsAccumulation:
    """db_manager.accumulate_token_daily_stats 行为测试。"""

    @pytest.mark.asyncio
    async def test_first_call_inserts_new_row(self, db_manager: DatabaseManager) -> None:
        """首次累加 INSERT 新行，token 计数正确。"""
        await db_manager.accumulate_token_daily_stats(
            user_id="user-001",
            date_value=date(2026, 7, 1),
            model="glm-4.6",
            call_type="process",
            ticket_id="TK-001",
            prompt_tokens=120,
            completion_tokens=80,
        )
        rows = await db_manager.list_token_daily_stats()
        assert len(rows) == 1
        assert rows[0]["user_id"] == "user-001"
        assert rows[0]["model"] == "glm-4.6"
        assert rows[0]["call_type"] == "process"
        assert rows[0]["prompt_tokens"] == 120
        assert rows[0]["completion_tokens"] == 80
        assert rows[0]["total_tokens"] == 200
        assert rows[0]["request_count"] == 1

    @pytest.mark.asyncio
    async def test_second_call_updates_existing_row(self, db_manager: DatabaseManager) -> None:
        """同 (user, date, model, call_type) 第二次累加 UPDATE，request_count=2。"""
        for _ in range(2):
            await db_manager.accumulate_token_daily_stats(
                user_id="user-001",
                date_value=date(2026, 7, 1),
                model="glm-4.6",
                call_type="process",
                ticket_id="TK-001",
                prompt_tokens=100,
                completion_tokens=50,
            )

        rows = await db_manager.list_token_daily_stats()
        assert len(rows) == 1
        assert rows[0]["prompt_tokens"] == 200
        assert rows[0]["completion_tokens"] == 100
        assert rows[0]["total_tokens"] == 300
        assert rows[0]["request_count"] == 2

    @pytest.mark.asyncio
    async def test_user_id_none_separate_row(self, db_manager: DatabaseManager) -> None:
        """user_id=None（系统级调用）与具体 user_id 在表中独立累加。"""
        # 系统调用
        await db_manager.accumulate_token_daily_stats(
            user_id=None,
            date_value=date(2026, 7, 1),
            model="glm-4.6",
            call_type="coordinator",
            ticket_id=None,
            prompt_tokens=20,
            completion_tokens=10,
        )
        await db_manager.accumulate_token_daily_stats(
            user_id=None,
            date_value=date(2026, 7, 1),
            model="glm-4.6",
            call_type="coordinator",
            ticket_id=None,
            prompt_tokens=15,
            completion_tokens=5,
        )
        # 用户调用
        await db_manager.accumulate_token_daily_stats(
            user_id="user-001",
            date_value=date(2026, 7, 1),
            model="glm-4.6",
            call_type="process",
            ticket_id="TK-001",
            prompt_tokens=100,
            completion_tokens=50,
        )

        rows = await db_manager.list_token_daily_stats()
        # 系统调用累加为 1 行 + 用户调用 1 行 = 2 行
        assert len(rows) == 2

        system_row = next(r for r in rows if r["user_id"] is None)
        user_row = next(r for r in rows if r["user_id"] == "user-001")
        assert system_row["request_count"] == 2
        assert system_row["total_tokens"] == 50  # (20+10) + (15+5)
        assert user_row["total_tokens"] == 150

    @pytest.mark.asyncio
    async def test_different_call_type_separate_rows(self, db_manager: DatabaseManager) -> None:
        """同一 user 但 call_type 不同 → 独立行。"""
        for call_type, prompt, completion in [
            ("intent", 50, 20),
            ("classify", 80, 30),
            ("process", 200, 100),
            ("review", 60, 25),
            ("coordinator", 40, 15),
            ("rag", 10, 5),
        ]:
            await db_manager.accumulate_token_daily_stats(
                user_id="user-001",
                date_value=date(2026, 7, 1),
                model="glm-4.6",
                call_type=call_type,
                ticket_id="TK-001",
                prompt_tokens=prompt,
                completion_tokens=completion,
            )

        rows = await db_manager.list_token_daily_stats()
        assert len(rows) == 6
        call_types = {r["call_type"] for r in rows}
        assert call_types == {"intent", "classify", "process", "review", "coordinator", "rag"}


class TestTraceAccumulateTokenDailyStats:
    """TraceManager.accumulate_token_daily_stats 通过 trace_id 关联 ticket/user。"""

    @pytest.mark.asyncio
    async def test_trace_accumulate_writes_token_daily_stats(self, db_manager: DatabaseManager) -> None:
        """trace_manager.accumulate_token_daily_stats 从 trace 反查 ticket_id + user_id。"""
        # 准备：创建 ticket + trace
        await db_manager.save_ticket({
            "ticket_id": "TK-001",
            "user_id": "user-001",
            "content": "test",
            "status": "received",
        })
        trace_manager = TraceManager(db_manager=db_manager)
        trace_id = await trace_manager.start_trace("TK-001")

        # 触发累加
        await trace_manager.accumulate_token_daily_stats(
            trace_id=trace_id,
            model="glm-4.6",
            call_type="process",
            prompt_tokens=100,
            completion_tokens=50,
        )

        rows = await db_manager.list_token_daily_stats()
        assert len(rows) == 1
        row = rows[0]
        assert row["user_id"] == "user-001"
        assert row["ticket_id"] == "TK-001"
        assert row["model"] == "glm-4.6"
        assert row["call_type"] == "process"
        assert row["total_tokens"] == 150

    @pytest.mark.asyncio
    async def test_trace_accumulate_no_ticket_user_id_none(self, db_manager: DatabaseManager) -> None:
        """trace_id 找不到 ticket 时 user_id=None（系统级调用）。"""
        trace_manager = TraceManager(db_manager=db_manager)
        trace_id = await trace_manager.start_trace("TK-NOTEXIST")

        await trace_manager.accumulate_token_daily_stats(
            trace_id=trace_id,
            model="glm-4.6",
            call_type="coordinator",
            prompt_tokens=20,
            completion_tokens=10,
        )

        rows = await db_manager.list_token_daily_stats()
        assert len(rows) == 1
        assert rows[0]["user_id"] is None


class TestCachedLLMClientTokenAccumulation:
    """CachedLLMClient._finalize_llm_span 写 token_daily_stats + trace.total_tokens。"""

    @pytest.mark.asyncio
    async def test_finalize_llm_span_writes_token_daily_stats(
        self, db_manager: DatabaseManager
    ) -> None:
        """LLM 调用完成后 token_daily_stats 累加 + traces.total_tokens 累加。"""
        # 准备 trace 上下文
        await db_manager.save_ticket({
            "ticket_id": "TK-001",
            "user_id": "user-001",
            "content": "test",
            "status": "received",
        })
        trace_manager = TraceManager(db_manager=db_manager)
        trace_id = await trace_manager.start_trace("TK-001")

        from src.multi_agent_system.core import trace as trace_module
        trace_module.current_trace_id.set(trace_id)

        # 注入 trace_manager 到 graph 模块（cached_client 通过它访问 db）
        with patch("src.multi_agent_system.workflow.graph._trace_manager", trace_manager):
            # 准备 cached client + mock OpenAI
            settings_mock = MagicMock()
            settings_mock.llm_api_key = "test"
            settings_mock.llm_base_url = "http://test"
            settings_mock.llm_model = "glm-4.6"
            settings_mock.cache_enabled = False

            with patch("src.multi_agent_system.config.Settings", return_value=settings_mock):
                client = CachedLLMClient()
                mock_openai = AsyncMock()
                mock_openai.chat.completions.create.return_value = _make_mock_response(
                    "ok", prompt=120, completion=80
                )
                client._client = mock_openai

                # 触发调用（task_type=process 映射到 call_type=process）
                # 显式传 model 避免 model_router 走 settings.model_routes（mock 会返回 MagicMock）
                await client.chat_completions_create(
                    messages=[{"role": "user", "content": "test"}],
                    model="glm-4.6",
                    cache=False,
                    task_type="process",
                )

        # 验证 token_daily_stats 写入
        rows = await db_manager.list_token_daily_stats()
        assert len(rows) == 1
        assert rows[0]["model"] == "glm-4.6"
        assert rows[0]["call_type"] == "process"
        assert rows[0]["prompt_tokens"] == 120
        assert rows[0]["completion_tokens"] == 80
        assert rows[0]["total_tokens"] == 200

        # 验证 traces.total_tokens 累加（v1.1 P0-1 修复）
        trace = await db_manager.get_trace_by_ticket("TK-001")
        assert trace is not None
        assert trace["total_tokens"] == 200  # 不再永远为 0
