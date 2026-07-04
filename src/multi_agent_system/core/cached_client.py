"""带缓存的 OpenAI 客户端封装。

自动缓存 chat.completions 调用结果，减少重复 Token 消耗。
"""

import time
from typing import Any

from loguru import logger
from openai import AsyncOpenAI

from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.metrics import metrics_collector
from src.multi_agent_system.core.trace import current_trace_id

__all__ = ["CachedLLMClient"]


class CachedLLMClient:
    """带缓存的 OpenAI 客户端封装，自动缓存 chat.completions 调用结果。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        settings = Settings()
        self.api_key = api_key or settings.llm_api_key
        self.base_url = base_url or settings.llm_base_url
        self.model = model or settings.llm_model
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        """延迟初始化 OpenAI 异步客户端。"""
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client

    async def chat_completions_create(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        cache: bool = True,
        task_type: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """调用 chat.completions.create，支持缓存和模型路由。

        Args:
            messages: 消息列表
            model: 模型名称（手动指定时优先级最高）
            temperature: 温度参数
            cache: 是否使用缓存（默认 True），设置为 False 跳过缓存
            task_type: 任务类型（如 classify/process/review/report），用于模型路由
            **kwargs: 其他传递给 API 的参数
        """
        from src.multi_agent_system.core.cache import _get_llm_cache

        # 模型选择优先级：手动指定 > task_type 路由 > 默认模型
        if model is not None:
            use_model = model
        elif task_type is not None:
            from src.multi_agent_system.core.model_router import get_model_router

            use_model = get_model_router().get_model(task_type)
        else:
            use_model = self.model
        llm_cache = _get_llm_cache()

        # 缓存未启用或显式禁用缓存，直接调用
        if llm_cache is None or not cache:
            logger.debug(f"[CachedLLMClient] 跳过缓存，直接调用 {use_model}")
            metrics_collector.record_cache_query(hit=False)
            trace_span = self._get_llm_span(
                use_model,
                task_type,
                messages=messages,
                temperature=temperature,
                kwargs=kwargs,
            )
            async with trace_span:
                start = time.time()
                try:
                    result = await self.client.chat.completions.create(
                        model=use_model,
                        messages=messages,
                        temperature=temperature,
                        **kwargs,
                    )
                    await self._finalize_llm_span(trace_span, result, use_model, start, task_type)
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

        # 生成缓存键
        cache_key = llm_cache._generate_key(
            model=use_model,
            messages=messages,
            temperature=temperature,
            **kwargs,
        )

        # 尝试从缓存获取
        cached_result = llm_cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"[CachedLLMClient] 缓存命中 {use_model}")
            metrics_collector.record_cache_query(hit=True)
            return cached_result

        # 缓存未命中，调用 API
        logger.debug(f"[CachedLLMClient] 缓存未命中，调用 {use_model}")
        metrics_collector.record_cache_query(hit=False)
        trace_span = self._get_llm_span(
            use_model,
            task_type,
            messages=messages,
            temperature=temperature,
            kwargs=kwargs,
        )
        async with trace_span:
            start = time.time()
            try:
                result = await self.client.chat.completions.create(
                    model=use_model,
                    messages=messages,
                    temperature=temperature,
                    **kwargs,
                )
                await self._finalize_llm_span(trace_span, result, use_model, start, task_type)
                # 缓存结果
                llm_cache.set(cache_key, result)
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

    @staticmethod
    async def _finalize_llm_span(
        trace_span: Any,
        result: Any,
        model: str,
        start: float,
        task_type: str | None,
    ) -> None:
        """LLM 调用完成后统一写入 token_usage metadata + 累加 trace.total_tokens。"""
        duration = time.time() - start
        metrics_collector.record_llm_call(
            model=model,
            task_type=task_type or "unknown",
            duration_seconds=duration,
        )
        usage = getattr(result, "usage", None)
        prompt_tokens = _numeric_usage(getattr(usage, "prompt_tokens", 0))
        completion_tokens = _numeric_usage(getattr(usage, "completion_tokens", 0))
        total_tokens = _numeric_usage(getattr(usage, "total_tokens", 0))
        choice = result.choices[0] if getattr(result, "choices", None) else None
        message = getattr(choice, "message", None)
        content = getattr(message, "content", "") if message is not None else ""
        finish_reason = getattr(choice, "finish_reason", None) if choice is not None else None
        trace_span.set_output({
            "content": content if isinstance(content, str) else "",
            "finish_reason": finish_reason if isinstance(finish_reason, str) else None,
        })
        trace_span.set_metadata({
            "model": model,
            "token_usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            "duration": round(duration, 4),
        })
        # 回写到 trace.total_tokens（修复 P0：原本永为 0）
        if total_tokens > 0:
            trace_id = current_trace_id.get()
            if trace_id:
                from src.multi_agent_system.workflow.graph import _trace_manager
                if _trace_manager is not None:
                    await _trace_manager.add_token_usage(trace_id, total_tokens)
                    # 累加 token_daily_stats（v2.0：Token 成本控制台数据源）
                    call_type = _map_call_type(task_type)
                    await _trace_manager.accumulate_token_daily_stats(
                        trace_id=trace_id,
                        model=model,
                        call_type=call_type,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )

    @staticmethod
    def _get_llm_span(
        model: str,
        task_type: str | None,
        messages: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        kwargs: dict[str, Any] | None = None,
    ):
        """获取 LLM 调用 span。"""
        if current_trace_id.get() is None:
            return _NoOpLLMSpan()
        from src.multi_agent_system.workflow.graph import _trace_manager
        if _trace_manager is None:
            return _NoOpLLMSpan()
        return _trace_manager.start_span(
            "chat_completions",
            "llm_call",
            input_data={
                "model": model,
                "task_type": task_type,
                "temperature": temperature,
                "message_count": len(messages or []),
                "messages_preview": _preview_messages(messages or []),
                "params": _safe_kwargs(kwargs or {}),
            },
        )


class _NoOpLLMSpan:
    """无 trace 时的空操作 LLM span。"""

    span_id = ""
    trace_id = ""

    def set_output(self, data: dict[str, Any]) -> None:
        pass

    def set_metadata(self, data: dict[str, Any]) -> None:
        pass

    def set_status(self, status: str) -> None:
        pass

    async def __aenter__(self) -> "_NoOpLLMSpan":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


def _preview_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """生成可展示的消息摘要，避免 trace 中写入过长上下文。"""
    preview: list[dict[str, str]] = []
    for message in messages[-6:]:
        role = str(message.get("role", "unknown"))
        content = str(message.get("content", ""))
        preview.append({
            "role": role,
            "content": _truncate_text(content, 1200),
        })
    return preview


def _safe_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """只保留适合出现在调试面板里的非敏感调用参数。"""
    allowed = {"max_tokens", "top_p", "presence_penalty", "frequency_penalty", "seed"}
    return {key: value for key, value in kwargs.items() if key in allowed}


def _numeric_usage(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        return int(value)
    return 0


# task_type → call_type 映射（详见 12 号设计文档第 7 节枚举）
_TASK_TYPE_TO_CALL_TYPE: dict[str, str] = {
    "intent": "intent",
    "ticket_intent": "intent",
    "classify": "classify",
    "classifier": "classify",
    "process": "process",
    "processor": "process",
    "react_processor": "process",
    "review": "review",
    "reviewer": "review",
    "coordinator": "coordinator",
    "report": "coordinator",
    "rag": "rag",
}


def _map_call_type(task_type: str | None) -> str:
    """task_type 映射到 token_daily_stats.call_type 枚举；未知值默认 process。"""
    if not task_type:
        return "process"
    return _TASK_TYPE_TO_CALL_TYPE.get(task_type, "process")


def _truncate_text(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}... (+{len(text) - max_length} 字)"
