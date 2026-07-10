"""工作流异常兜底测试。

验证 _run_workflow 异常分支的三级兜底逻辑：
1. _fallback_to_human_review 失败 -> 标记工单 failed + 广播
2. db_tool.save_ticket 也失败 -> 仅记录 critical 日志，不抛出
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from loguru import logger as loguru_logger

from src.multi_agent_system.api.routes import _run_workflow, _safe_fallback_to_human_review


def _bridge_loguru_to_caplog(level: str = "WARNING") -> int:
    """桥接 loguru -> stdlib logging，使 pytest caplog 可捕获。

    返回 handler_id，测试结束后用于移除。
    """

    def _sink(message):
        record = message.record
        logging.getLogger(record["name"]).log(
            record["level"].no,
            record["message"],
        )

    return loguru_logger.add(_sink, level=level)


class _StubWorkflow:
    """模拟一个会在 astream 中抛异常的工作流。"""

    async def astream(self, state):
        # 第一节点正常，让 _run_workflow 先写入 current_state
        yield {"process": {"status": "processing", "processing_result": "处理中"}}
        # 第二节点抛异常
        raise RuntimeError("下游服务爆炸")


@pytest.fixture
def fallback_app():
    """构建一个最小化的 app 桩件，所有依赖用 mock。"""
    app = MagicMock()
    app.state = MagicMock()
    app.state.workflow = _StubWorkflow()
    app.state.db_tool = MagicMock()
    # 默认 get_ticket 返回已存在工单
    app.state.db_tool.get_ticket = AsyncMock(return_value={
        "ticket_id": "T-FALLBACK",
        "content": "测试工单",
        "status": "processing",
    })
    app.state.db_tool.save_ticket = AsyncMock(return_value=None)
    app.state.db_manager = MagicMock()
    app.state.db_manager.create_pending_review = AsyncMock(return_value=None)
    app.state.coordinator = None  # 跳过 AI 建议
    app.state.settings = MagicMock()
    # _run_workflow 主流程中 trace_manager 检查路径需要 falsy 跳过
    app.state.trace_manager = None
    # get_pending_review_by_ticket 在 review_requested 广播中被 await，返回 None 即可
    app.state.db_manager.get_pending_review_by_ticket = AsyncMock(return_value=None)
    return app


class TestSafeFallback:
    """三级兜底单元测试（直接调用 _safe_fallback_to_human_review）。"""

    @pytest.mark.asyncio
    async def test_second_level_marks_failed(self, fallback_app):
        """第二层兜底：_fallback_to_human_review 抛异常时，标记工单 failed。

        场景：db_manager.create_pending_review 失败，触发第一层异常；
        但 db_tool.save_ticket 仍可用，应走第二层标记 failed。
        """
        # 让 create_pending_review 抛异常，使 _fallback_to_human_review 整体失败
        fallback_app.state.db_manager.create_pending_review = AsyncMock(
            side_effect=RuntimeError("DB 宕机")
        )

        # 捕获 save_ticket 调用参数
        saved = {}

        async def _capture_save(data):
            saved.update(data)

        fallback_app.state.db_tool.save_ticket = AsyncMock(side_effect=_capture_save)

        # 不应抛出
        await _safe_fallback_to_human_review(
            fallback_app,
            "T-FALLBACK",
            {"processing_result": "x"},
            {"ticket_id": "T-FALLBACK", "content": "测试"},
            "原始异常 X",
        )

        # 验证标记 failed，且 error 包含双重错误信息
        assert saved["status"] == "failed"
        assert saved["ticket_id"] == "T-FALLBACK"
        assert "原始异常 X" in saved["error"]
        assert "DB 宕机" in saved["error"]

    @pytest.mark.asyncio
    async def test_third_level_logs_critical(self, fallback_app, caplog):
        """第三层兜底：save_ticket 也抛异常时，仅记录 critical 日志。"""
        # 第一层就失败
        fallback_app.state.db_manager.create_pending_review = AsyncMock(
            side_effect=RuntimeError("pending review 写入失败")
        )
        # 第二层也失败
        fallback_app.state.db_tool.save_ticket = AsyncMock(
            side_effect=RuntimeError("save_ticket 也崩溃")
        )

        handler_id = _bridge_loguru_to_caplog("CRITICAL")
        caplog.set_level(logging.CRITICAL)
        try:
            # 不应抛出
            await _safe_fallback_to_human_review(
                fallback_app,
                "T-FALLBACK",
                {"processing_result": "x"},
                {"ticket_id": "T-FALLBACK", "content": "测试"},
                "原始异常 X",
            )
        finally:
            loguru_logger.remove(handler_id)

        # 验证 critical 日志包含工单 ID 和两层错误
        critical_msgs = [
            r.message for r in caplog.records if r.levelname == "CRITICAL"
        ]
        assert any("T-FALLBACK" in m for m in critical_msgs), (
            f"critical 日志应包含工单 ID，实际: {critical_msgs}"
        )
        assert any("完全无法处理" in m for m in critical_msgs), (
            f"critical 日志应包含 '完全无法处理'，实际: {critical_msgs}"
        )


class TestRunWorkflowIntegration:
    """_run_workflow 异常分支端到端验证。"""

    @pytest.mark.asyncio
    async def test_run_workflow_does_not_wait_between_nodes(self, fallback_app):
        """节点广播后不应人为等待，页面进度只跟随真实工作流状态。"""
        fallback_app.state.settings.workflow_node_delay_seconds = 1.2

        with patch(
            "src.multi_agent_system.api.routes.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock:
            await _run_workflow(
                fallback_app,
                "T-FALLBACK",
                {
                    "ticket_id": "T-FALLBACK",
                    "content": "测试工单",
                    "status": "processing",
                    "__trace_id__": None,
                },
            )

        sleep_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_workflow_exception_triggers_safe_fallback(self, fallback_app):
        """_run_workflow 捕获工作流异常后调用 _safe_fallback_to_human_review，
        默认场景（第一层成功）应正常完成不抛出。
        """
        # 第一层成功路径：create_pending_review 正常
        # 但 _StubWorkflow 会抛 RuntimeError，进入异常分支
        # 期望：工单被标记为 pending_human_review
        saved = {}

        async def _capture_save(data):
            saved.update(data)

        fallback_app.state.db_tool.save_ticket = AsyncMock(side_effect=_capture_save)

        # 不应抛出
        await _run_workflow(
            fallback_app,
            "T-FALLBACK",
            {
                "ticket_id": "T-FALLBACK",
                "content": "测试工单",
                "status": "processing",
                "__trace_id__": None,
            },
        )

        # _fallback_to_human_review 内部 save_ticket 写过 pending_human_review
        assert saved.get("status") == "pending_human_review"
        assert saved.get("error") == "下游服务爆炸"

    @pytest.mark.asyncio
    async def test_run_workflow_second_fallback_marks_failed(self, fallback_app):
        """_run_workflow 异常分支 + _fallback_to_human_review 失败 -> 工单 failed。"""
        # 第一层失败
        fallback_app.state.db_manager.create_pending_review = AsyncMock(
            side_effect=RuntimeError("DB 宕机")
        )

        saved = {}

        async def _capture_save(data):
            saved.update(data)

        fallback_app.state.db_tool.save_ticket = AsyncMock(side_effect=_capture_save)

        # 不应抛出
        await _run_workflow(
            fallback_app,
            "T-FALLBACK",
            {
                "ticket_id": "T-FALLBACK",
                "content": "测试工单",
                "status": "processing",
                "__trace_id__": None,
            },
        )

        # 最终状态为 failed
        assert saved.get("status") == "failed"
        assert "下游服务爆炸" in saved.get("error", "")
        assert "DB 宕机" in saved.get("error", "")
