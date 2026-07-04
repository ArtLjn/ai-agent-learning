# Production Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement production-grade stability, performance, and observability features for the multi-agent ticket processing system

**Architecture:** 
- Unified exception handling layer with exponential backoff retry and multi-level fallback chain
- LLM result caching layer to reduce token costs and improve response time
- Model routing layer to select appropriate model based on task complexity
- Observability endpoints for health checks and performance metrics
- Graceful shutdown support for server processes

**Tech Stack:**
- Python 3.12+, asyncio
- loguru for structured logging
- cachetools for LRU cache
- tenacity for retry logic
- FastAPI for HTTP endpoints

---

## Task 1: Exceptions and Retry Layer (Completed)

**Files:**
- Create: `src/multi_agent_system/core/exceptions.py`
- Create: `src/multi_agent_system/core/retry.py`
- Create: `src/multi_agent_system/core/fallback.py`
- Create: `src/multi_agent_system/core/__init__.py`
- Test: `tests/core/test_exceptions.py`, `tests/core/test_retry.py`, `tests/core/test_fallback.py`

- [x] **Step 1: Define exception hierarchy**

```python
# src/multi_agent_system/core/exceptions.py
__all__ = ["RetryableError", "NonRetryableError", "FallbackExhaustedError"]

class RetryableError(Exception):
    """可重试的临时性错误。"""
    def __init__(self, message: str = "", *, cause: Exception | None = None) -> None:
        self.cause = cause
        super().__init__(message)

class NonRetryableError(Exception):
    """不可重试的业务/参数错误。"""
    def __init__(self, message: str = "", *, cause: Exception | None = None) -> None:
        self.cause = cause
        super().__init__(message)

class FallbackExhaustedError(Exception):
    """重试和降级均已耗尽。"""
    def __init__(self, message: str = "", *, cause: Exception | None = None) -> None:
        self.cause = cause
        super().__init__(message)
```

- [x] **Step 2: Implement @with_retry decorator**

```python
# src/multi_agent_system/core/retry.py
import asyncio
from functools import wraps
from typing import Any, Callable, Coroutine, TypeVar

from loguru import logger

from src.multi_agent_system.core.exceptions import NonRetryableError, RetryableError

__all__ = ["with_retry"]

T = TypeVar("T")

def with_retry(
    max_retries: int = 3,
    backoff_base: float = 2.0,
    retryable_exceptions: tuple[type[Exception], ...] = (RetryableError,),
    fallback: Callable[..., Any] | None = None,
) -> Callable[..., Any]:
    def decorator(
        func: Callable[..., Coroutine[Any, Any, T]],
    ) -> Callable[..., Coroutine[Any, Any, T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except NonRetryableError as e:
                    logger.warning(f"[{func.__name__}] 不可重试异常，跳过重试: {e}")
                    last_exception = e
                    break
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        wait = backoff_base**attempt
                        logger.warning(f"[{func.__name__}] 可重试异常 (第{attempt + 1}次)，{wait}s后重试: {e}")
                        await asyncio.sleep(wait)
                    else:
                        logger.error(f"[{func.__name__}] 重试耗尽 ({max_retries}次): {e}")
                except Exception as e:
                    logger.warning(f"[{func.__name__}] 未知异常，跳过重试: {e}")
                    last_exception = e
                    break

            if fallback is not None:
                logger.info(f"[{func.__name__}] 触发降级函数")
                result = fallback(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    return await result
                return result

            from src.multi_agent_system.core.exceptions import FallbackExhaustedError
            raise FallbackExhaustedError(
                f"[{func.__name__}] 重试耗尽且无降级函数",
                cause=last_exception,
            ) from last_exception
        return wrapper
    return decorator
```

- [x] **Step 3: Implement FallbackRegistry**

```python
# src/multi_agent_system/core/fallback.py
from typing import Any, Callable, Coroutine, TypeVar, Dict, List

from loguru import logger

__all__ = ["FallbackRegistry"]

T = TypeVar("T")

class FallbackRegistry:
    """降级函数注册表，支持多级降级链。"""

    def __init__(self) -> None:
        self._fallbacks: Dict[str, List[Callable[..., Any]]] = {}

    def register(self, name: str, fallback: Callable[..., Any]) -> None:
        """注册降级函数。同名函数按注册顺序形成降级链。"""
        if name not in self._fallbacks:
            self._fallbacks[name] = []
        self._fallbacks[name].append(fallback)

    def get(self, name: str) -> List[Callable[..., Any]]:
        """获取指定名称的降级函数列表。"""
        return self._fallbacks.get(name, [])

    async def execute(self, name: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """执行指定名称的降级链，返回第一个成功的结果。
        所有结果自动添加 "fallback": True 标记。
        """
        fallbacks = self.get(name)
        if not fallbacks:
            logger.warning(f"[{name}] 没有可用的降级函数")
            return {"error": "no fallback available", "fallback": True}

        last_exception: Exception | None = None
        for idx, fallback in enumerate(fallbacks):
            try:
                result = fallback(*args, **kwargs)
                if isinstance(result, Coroutine):
                    result = await result
                if isinstance(result, dict):
                    result["fallback"] = True
                else:
                    result = {"result": result, "fallback": True}
                logger.info(f"[{name}] 降级函数 {idx + 1} 执行成功")
                return result
            except Exception as e:
                last_exception = e
                logger.warning(f"[{name}] 降级函数 {idx + 1} 失败: {e}")
                continue

        logger.error(f"[{name}] 所有降级函数均失败")
        return {
            "error": "all fallbacks failed",
            "cause": str(last_exception) if last_exception else None,
            "fallback": True,
        }

# 全局单例
fallback_registry = FallbackRegistry()
```

- [x] **Step 4: Create __init__.py**

```python
# src/multi_agent_system/core/__init__.py
from src.multi_agent_system.core.exceptions import (
    FallbackExhaustedError,
    NonRetryableError,
    RetryableError,
)
from src.multi_agent_system.core.fallback import FallbackRegistry, fallback_registry
from src.multi_agent_system.core.retry import with_retry

__all__ = [
    "with_retry",
    "FallbackRegistry",
    "fallback_registry",
    "RetryableError",
    "NonRetryableError",
    "FallbackExhaustedError",
]
```

- [x] **Step 5: Write tests**
- [x] **Step 6: Run tests to verify they pass**
- [x] **Step 7: Commit**

---

## Task 2: Structured Logging with Trace ID

**Files:**
- Create: `src/multi_agent_system/core/logging.py`
- Modify: `src/multi_agent_system/workflow/graph.py` (add trace_id generation)
- Test: `tests/core/test_logging.py`

- [ ] **Step 1: Implement structured logging utilities**

```python
# src/multi_agent_system/core/logging.py
import contextvars
import time
from typing import Any, Dict, Optional
from uuid import uuid4

from loguru import logger

__all__ = [
    "trace_id_var",
    "log_context",
    "get_trace_id",
    "generate_trace_id",
    "structured_logger",
]

# 全局 trace_id 上下文变量
trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "trace_id", default=None
)


def generate_trace_id() -> str:
    """生成唯一 trace_id，格式：随机16位十六进制字符串"""
    return uuid4().hex[:16]


def get_trace_id() -> Optional[str]:
    """获取当前请求的 trace_id"""
    return trace_id_var.get()


class log_context:
    """日志上下文管理器，自动绑定 trace_id 和其他元数据。

    示例:
    ```python
    with log_context(agent="classifier", task="classification"):
        logger.info("处理请求")  # 自动带上 trace_id=xxx agent=classifier
    ```
    """

    def __init__(self, **kwargs: Any) -> None:
        self.extra = kwargs
        self.token: Optional[contextvars.Token] = None
        self.start_time: float = 0.0

    def __enter__(self) -> "log_context":
        self.start_time = time.perf_counter()
        # 如果没有 trace_id，自动生成
        if get_trace_id() is None:
            trace_id = generate_trace_id()
            self.token = trace_id_var.set(trace_id)
            self.extra["trace_id"] = trace_id
        else:
            self.extra["trace_id"] = get_trace_id()

        # 绑定额外元数据到 loguru 上下文
        logger.contextualize(**self.extra)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        # 计算耗时
        duration_ms = (time.perf_counter() - self.start_time) * 1000
        logger.info(f"执行完成，耗时 {duration_ms:.2f}ms")

        # 恢复 trace_id 上下文
        if self.token is not None:
            trace_id_var.reset(self.token)


def structured_logger(
    message: str, level: str = "INFO", **kwargs: Any
) -> None:
    """结构化日志工具函数，自动带上 trace_id。"""
    trace_id = get_trace_id()
    extra: Dict[str, Any] = {"trace_id": trace_id, **kwargs}
    log_method = getattr(logger, level.lower(), logger.info)
    log_method(message, **extra)
```

- [ ] **Step 2: Add loguru formatter configuration to main module**

```python
# Add to src/multi_agent_system/__init__.py
from loguru import logger
import sys

# 配置结构化日志格式
logger.configure(
    handlers=[
        {
            "sink": sys.stdout,
            "format": (
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "{extra[trace_id]} | {message}"
            ),
        }
    ]
)
```

- [ ] **Step 3: Add trace_id generation in workflow graph**

```python
# Modify src/multi_agent_system/workflow/graph.py
from src.multi_agent_system.core.logging import log_context, generate_trace_id

def create_initial_state(content: str, ticket_id: str | None = None) -> TicketState:
    """创建工单初始状态。"""
    if ticket_id is None:
        ticket_id = generate_trace_id()  # Use trace_id as ticket_id for correlation

    # Generate trace_id and bind to context for this request
    log_context(ticket_id=ticket_id).__enter__()

    return TicketState(
        ticket_id=ticket_id,
        content=content,
        category=None,
        priority=None,
        processing_result=None,
        review_score=None,
        retry_count=0,
        status="received",
        messages=[],
        error=None,
    )
```

- [ ] **Step 4: Write test for logging utilities**
- [ ] **Step 5: Run tests to verify they pass**
- [ ] **Step 6: Commit with message: `feat: 实现结构化日志和trace_id链路追踪`**

---

## Task 3: Integrate Exception Handling into Existing Agents

**Files:**
- Modify: `src/multi_agent_system/agents/classifier.py`
- Modify: `src/multi_agent_system/agents/processor.py`
- Modify: `src/multi_agent_system/agents/reviewer.py`
- Modify: `src/multi_agent_system/agents/coordinator.py`
- Modify: `src/multi_agent_system/workflow/graph.py`
- Test: `tests/agents/test_agent_integration.py`

- [ ] **Step 1: Update ClassifierAgent with @with_retry and fallback registry**

```python
# Modify src/multi_agent_system/agents/classifier.py
from src.multi_agent_system.core import with_retry, fallback_registry
from src.multi_agent_system.core.exceptions import RetryableError, NonRetryableError
from openai import APIError, APIConnectionError, AuthenticationError, RateLimitError

# Register fallback
fallback_registry.register("classifier.classify", _classify_by_fallback)

# Update _classify_by_llm method with retry
@with_retry(
    max_retries=3,
    backoff_base=2.0,
    retryable_exceptions=(
        APIError, 
        APIConnectionError, 
        RateLimitError,
        RetryableError,
    ),
    fallback=lambda self, content: fallback_registry.execute("classifier.classify", content),
)
async def _classify_by_llm(self, content: str) -> dict:
    try:
        response = await self.client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": f"请分类以下工单：\n{content}"},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
    except AuthenticationError as e:
        raise NonRetryableError(f"OpenAI 认证失败: {e}", cause=e) from e
    except (APIError, APIConnectionError, RateLimitError) as e:
        raise RetryableError(f"OpenAI API 调用失败: {e}", cause=e) from e
    
    raw = response.choices[0].message.content or "{}"
    logger.info(f"🤖 [Classifier] LLM 响应: {raw}")
    try:
        result = _parse_json_response(raw)
    except json.JSONDecodeError as e:
        raise NonRetryableError(f"JSON 解析失败: {e}", cause=e) from e

    # Validation
    category = result.get("category", "")
    priority = result.get("priority", "")
    reason = result.get("reason", "")

    if category not in _VALID_CATEGORIES:
        logger.warning(f"LLM 返回非法分类 '{category}'，降级到 inquiry")
        category = TicketCategory.INQUIRY.value

    if priority not in _VALID_PRIORITIES:
        logger.warning(f"LLM 返回非法优先级 '{priority}'，降级到 P3")
        priority = TicketPriority.P3.value

    return {
        "category": category,
        "priority": priority,
        "reason": reason,
    }
```

- [ ] **Step 2: Apply same pattern to ProcessorAgent, ReviewerAgent, CoordinatorAgent**
- [ ] **Step 3: Update workflow graph nodes to use @with_retry decorator**
- [ ] **Step 4: Remove hardcoded _MAX_RETRIES in graph.py, use from config**
- [ ] **Step 5: Write integration tests for retry and fallback behavior**
- [ ] **Step 6: Run tests to verify they pass**
- [ ] **Step 7: Commit with message: `feat: 所有Agent接入统一重试降级机制`**

---

## Task 4: LLM Caching Layer

**Files:**
- Create: `src/multi_agent_system/core/cache.py`
- Create: `src/multi_agent_system/core/cached_client.py`
- Modify: `src/multi_agent_system/config.py` (add cache config)
- Modify: All Agent classes to use CachedLLMClient
- Test: `tests/core/test_cache.py`, `tests/core/test_cached_client.py`

- [ ] **Step 1: Add cache config to settings**

```python
# Add to src/multi_agent_system/config.py
class Settings(BaseSettings):
    # ... existing config ...
    
    # Cache configuration
    cache_enabled: bool = True
    cache_max_size: int = 512
    cache_ttl: int = 300  # seconds
```

- [ ] **Step 2: Implement LLMCache**

```python
# src/multi_agent_system/core/cache.py
import time
import hashlib
import json
from typing import Any, Dict, Optional, Tuple
from cachetools import LRUCache
from loguru import logger

__all__ = ["LLMCache", "llm_cache"]


class LLMCache:
    """LLM 调用结果缓存，基于 LRU 算法，支持 TTL 过期。"""

    def __init__(self, max_size: int = 512, ttl: int = 300) -> None:
        self.cache = LRUCache(maxsize=max_size)
        self.ttl = ttl
        self.hits = 0
        self.misses = 0

    def _generate_key(
        self, 
        model: str, 
        messages: list[Dict[str, Any]], 
        temperature: float,
        **kwargs: Any
    ) -> str:
        """生成缓存键：对模型名称、消息内容、温度等参数做哈希。"""
        cache_params = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            **kwargs
        }
        # 序列化并哈希
        serialized = json.dumps(cache_params, sort_keys=True).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        """获取缓存，过期返回 None。"""
        entry = self.cache.get(key)
        if entry is None:
            self.misses += 1
            return None
        
        value, expire_at = entry
        if time.time() > expire_at:
            del self.cache[key]
            self.misses += 1
            return None
        
        self.hits += 1
        return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """设置缓存，ttl 默认使用初始化参数。"""
        if ttl is None:
            ttl = self.ttl
        expire_at = time.time() + ttl
        self.cache[key] = (value, expire_at)

    def clear(self) -> None:
        """清空缓存。"""
        self.cache.clear()
        self.hits = 0
        self.misses = 0

    @property
    def hit_rate(self) -> float:
        """缓存命中率。"""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息。"""
        return {
            "size": len(self.cache),
            "max_size": self.cache.maxsize,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hit_rate,
            "ttl": self.ttl,
        }


# 全局单例
from src.multi_agent_system.config import Settings
settings = Settings()
llm_cache = LLMCache(
    max_size=settings.cache_max_size,
    ttl=settings.cache_ttl,
) if settings.cache_enabled else None
```

- [ ] **Step 3: Implement CachedLLMClient**

```python
# src/multi_agent_system/core/cached_client.py
from typing import Any, Dict, Optional
from openai import AsyncOpenAI
from loguru import logger

from src.multi_agent_system.core.cache import llm_cache
from src.multi_agent_system.config import Settings

__all__ = ["CachedLLMClient"]


class CachedLLMClient:
    """带缓存的 OpenAI 客户端封装，自动缓存 chat.completions 调用结果。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        settings = Settings()
        self.api_key = api_key or settings.llm_api_key
        self.base_url = base_url or settings.llm_base_url
        self.model = model or settings.llm_model
        self._client: Optional[AsyncOpenAI] = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client

    async def chat_completions_create(
        self,
        messages: list[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        cache: bool = True,
        **kwargs: Any
    ) -> Any:
        """调用 chat.completions.create，支持缓存。
        
        Args:
            cache: 是否使用缓存（默认 True），设置为 False 跳过缓存（如审核、报告等场景）
        """
        use_model = model or self.model
        
        # 缓存未启用或显式禁用缓存，直接调用
        if llm_cache is None or not cache:
            logger.debug(f"[CachedLLMClient] 跳过缓存，直接调用 {use_model}")
            return await self.client.chat.completions.create(
                model=use_model,
                messages=messages,
                temperature=temperature,
                **kwargs
            )
        
        # 生成缓存键
        cache_key = llm_cache._generate_key(
            model=use_model,
            messages=messages,
            temperature=temperature,
            **kwargs
        )
        
        # 尝试从缓存获取
        cached_result = llm_cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"[CachedLLMClient] 缓存命中 {use_model}")
            return cached_result
        
        # 缓存未命中，调用 API
        logger.debug(f"[CachedLLMClient] 缓存未命中，调用 {use_model}")
        result = await self.client.chat.completions.create(
            model=use_model,
            messages=messages,
            temperature=temperature,
            **kwargs
        )
        
        # 缓存结果
        llm_cache.set(cache_key, result)
        return result
```

- [ ] **Step 4: Update all Agents to use CachedLLMClient instead of AsyncOpenAI**
- [ ] **Step 5: Write tests for cache behavior (hit, miss, TTL, disabled)**
- [ ] **Step 6: Run tests to verify they pass**
- [ ] **Step 7: Commit with message: `feat: 实现LLM调用缓存层，降低Token成本`**

---

## Task 5: Model Routing Layer

**Files:**
- Create: `src/multi_agent_system/core/model_router.py`
- Modify: `src/multi_agent_system/config.py` (add model routing config)
- Modify: All Agent classes to support dynamic model selection
- Test: `tests/core/test_model_router.py`

- [ ] **Step 1: Add model routing config to settings**

```python
# Add to src/multi_agent_system/config.py
class Settings(BaseSettings):
    # ... existing config ...
    
    # Model routing configuration
    model_routes: Dict[str, str] = {
        "classify": "qwen3:4b",  # 分类任务用轻量模型
        "process": "qwen3:8b",   # 处理任务用标准模型
        "review": "qwen3:8b",    # 审核任务用标准模型
        "report": "qwen3:14b",   # 报告生成用大模型
        "default": "qwen3:8b",   # 默认模型
    }
    fallback_model: str = "qwen3:8b"  # 路由失败时的降级模型
```

- [ ] **Step 2: Implement ModelRouter**

```python
# src/multi_agent_system/core/model_router.py
from typing import Dict, Optional
from loguru import logger

from src.multi_agent_system.config import Settings

__all__ = ["ModelRouter", "model_router"]


class ModelRouter:
    """模型路由器，根据任务类型选择合适的模型。"""

    def __init__(self, routes: Dict[str, str], fallback_model: str) -> None:
        self.routes = routes
        self.fallback_model = fallback_model

    def get_model(self, task_type: str) -> str:
        """根据任务类型获取模型名称。
        
        任务类型包括: classify, process, review, report
        """
        model = self.routes.get(task_type.lower())
        if model is None:
            logger.warning(f"[ModelRouter] 未知任务类型 {task_type}，使用默认模型 {self.fallback_model}")
            return self.fallback_model
        
        logger.debug(f"[ModelRouter] 任务 {task_type} 路由到模型 {model}")
        return model


# 全局单例
settings = Settings()
model_router = ModelRouter(
    routes=settings.model_routes,
    fallback_model=settings.fallback_model,
)
```

- [ ] **Step 3: Update CachedLLMClient to support task_type parameter**
- [ ] **Step 4: Update Agent class constructors to accept task_type parameter**
- [ ] **Step 5: Update workflow graph to pass correct task_type to agents**
- [ ] **Step 6: Write tests for model routing logic**
- [ ] **Step 7: Run tests to verify they pass**
- [ ] **Step 8: Commit with message: `feat: 实现模型路由层，根据任务复杂度选择最优模型`**

---

## Task 6: Asynchronous Concurrent Optimization

**Files:**
- Modify: `src/multi_agent_system/workflow/graph.py` (add concurrent execution)
- Test: `tests/workflow/test_concurrent_execution.py`

- [ ] **Step 1: Identify independent nodes that can run concurrently**
- [ ] **Step 2: Implement asyncio.gather for parallel execution**
- [ ] **Step 3: Add error isolation for concurrent tasks**
- [ ] **Step 4: Write concurrent execution tests**
- [ ] **Step 5: Run tests to verify they pass**
- [ ] **Step 6: Commit with message: `feat: 实现独立任务并发执行，提升吞吐性能`**

---

## Task 7: Health Check and Metrics

**Files:**
- Create: `src/multi_agent_system/core/metrics.py`
- Modify: `src/multi_agent_system/api/app.py` (add health/metrics endpoints, graceful shutdown)
- Test: `tests/api/test_health.py`, `tests/api/test_metrics.py`

- [ ] **Step 1: Implement MetricsCollector**

```python
# src/multi_agent_system/core/metrics.py
from typing import Any, Dict
from collections import defaultdict
import time

__all__ = ["MetricsCollector", "metrics_collector"]


class MetricsCollector:
    """指标收集器，统计调用次数、耗时、错误率等。"""

    def __init__(self) -> None:
        self.counters: Dict[str, int] = defaultdict(int)
        self.timers: Dict[str, list[float]] = defaultdict(list)
        self.errors: Dict[str, int] = defaultdict(int)

    def increment(self, metric: str, value: int = 1) -> None:
        """计数器递增。"""
        self.counters[metric] += value

    def record_timing(self, metric: str, duration_ms: float) -> None:
        """记录耗时。"""
        self.timers[metric].append(duration_ms)

    def record_error(self, metric: str) -> None:
        """记录错误。"""
        self.errors[metric] += 1

    def get_stats(self) -> Dict[str, Any]:
        """获取所有统计指标。"""
        stats: Dict[str, Any] = {
            "counters": dict(self.counters),
            "errors": dict(self.errors),
            "timers": {},
        }

        for metric, timings in self.timers.items():
            if not timings:
                continue
            stats["timers"][metric] = {
                "count": len(timings),
                "min": min(timings),
                "max": max(timings),
                "avg": sum(timings) / len(timings),
                "p95": sorted(timings)[int(len(timings) * 0.95)],
            }

        # 计算错误率
        for metric in self.counters:
            total = self.counters[metric]
            errors = self.errors.get(metric, 0)
            stats[f"{metric}_error_rate"] = errors / total if total > 0 else 0.0

        # 添加缓存统计
        from src.multi_agent_system.core.cache import llm_cache
        if llm_cache is not None:
            stats["cache"] = llm_cache.get_stats()

        return stats

    def clear(self) -> None:
        """清空所有指标。"""
        self.counters.clear()
        self.timers.clear()
        self.errors.clear()


# 全局单例
metrics_collector = MetricsCollector()
```

- [ ] **Step 2: Add health endpoint to FastAPI app**

```python
# Add to src/multi_agent_system/api/app.py
@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """健康检查端点。"""
    # 检查 LLM 连通性
    try:
        from openai import AsyncOpenAI
        from src.multi_agent_system.config import Settings
        settings = Settings()
        client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        # 简单测试模型列表调用
        await client.models.list(timeout=5)
        llm_health = "ok"
    except Exception as e:
        llm_health = f"error: {str(e)}"

    return {
        "status": "ok" if llm_health == "ok" else "unhealthy",
        "llm": llm_health,
        "uptime": int(time.time() - app.state.start_time),
        "version": "1.0.0",
    }
```

- [ ] **Step 3: Add metrics endpoint**

```python
@app.get("/metrics")
async def get_metrics() -> Dict[str, Any]:
    """性能指标端点。"""
    return metrics_collector.get_stats()
```

- [ ] **Step 4: Add graceful shutdown support**
- [ ] **Step 5: Add metrics recording throughout the codebase**
- [ ] **Step 6: Write tests for health and metrics endpoints**
- [ ] **Step 7: Run tests to verify they pass**
- [ ] **Step 8: Commit with message: `feat: 实现健康检查、性能指标和优雅关闭`**

---

## Task 8: Final Integration and Documentation

**Files:**
- Modify: `requirements.txt` (add cachetools)
- Update: `README.md` (add production features documentation)
- Run: All tests to verify full integration

- [ ] **Step 1: Add cachetools to requirements.txt**
- [ ] **Step 2: Update README with new production features**
- [ ] **Step 3: Run full test suite to ensure no regression**
- [ ] **Step 4: Commit with message: `feat: production优化完成，所有功能上线`**
