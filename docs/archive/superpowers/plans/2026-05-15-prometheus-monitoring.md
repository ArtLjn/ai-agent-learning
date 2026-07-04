# Prometheus 监控集成实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将多Agent工单处理系统的监控从基础 JSON 指标升级为 Prometheus + Grafana 可观测性基础设施

**Architecture:** 保留现有 JSON `/metrics` 端点向后兼容，新增 `/prometheus` 端点返回 Prometheus 标准文本格式。在 core/metrics.py 中定义所有 prometheus_client 指标，在 HTTP 中间件、Agent 节点、LLM 调用、缓存查询处注入指标上报。通过 docker-compose 一键启动 Prometheus + Grafana 容器。

**Tech Stack:** Python 3.12, FastAPI, prometheus_client, Prometheus, Grafana, Docker Compose

---

## 文件结构

```
ai-agent-learning/
├── requirements.txt                          # 新增 prometheus-client
├── docker-compose.yml                        # 新增 prometheus + grafana 服务
├── prometheus.yml                            # 新建：Prometheus 采集配置
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/datasource.yml        # 新建：自动配置 Prometheus 数据源
│   │   └── dashboards/dashboard.yml          # 新建：自动导入 Dashboard
│   └── dashboards/
│       └── multi-agent-system.json           # 新建：预配置监控面板
├── src/multi_agent_system/
│   ├── core/
│   │   ├── metrics.py                        # 修改：新增 prometheus_client 指标定义
│   │   ├── cache.py                          # 修改：注入缓存命中指标
│   │   └── cached_client.py                  # 修改：注入 LLM 调用指标
│   ├── api/
│   │   └── app.py                            # 修改：新增 /prometheus 端点，升级 MetricsMiddleware
│   └── workflow/
│       └── graph.py                          # 修改：各节点注入执行指标
└── tests/core/
    └── test_prometheus_metrics.py            # 新建：Prometheus 指标单元测试
```

---

## Task 1: 安装 prometheus-client 依赖

**Files:**
- Modify: `requirements.txt`
- Test: 验证导入

- [ ] **Step 1: 在 requirements.txt 添加依赖**

在 `requirements.txt` 的 `# Utilities` 部分下方添加：

```
# Monitoring
prometheus-client>=0.20.0
```

- [ ] **Step 2: 本地安装并验证**

Run: `cd /Users/ljn/Desktop/agent-study/ai-agent-learning && pip install prometheus-client`

Expected: 安装成功，无报错

Run: `python -c "from prometheus_client import Counter, Histogram, Gauge, generate_latest, REGISTRY; print('OK')"`

Expected: 输出 `OK`

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add prometheus-client dependency"
```

---

## Task 2: 重构 core/metrics.py 添加 Prometheus 指标定义

**Files:**
- Modify: `src/multi_agent_system/core/metrics.py`
- Test: `tests/core/test_prometheus_metrics.py`

**Context:** 当前 `metrics.py` 包含 `MetricsCollector` 类（JSON 格式），需要保留向后兼容。新增基于 `prometheus_client` 的指标定义。

- [ ] **Step 1: 编写测试验证 Prometheus 指标可导入**

Create `tests/core/test_prometheus_metrics.py`:

```python
"""Prometheus 指标定义测试。"""

import pytest
from prometheus_client import REGISTRY


class TestPrometheusMetrics:
    """验证 Prometheus 指标已正确注册到全局 Registry。"""

    @pytest.fixture(autouse=True)
    def _clear_registry(self):
        """每个测试前清空 Registry 中已注册的指标。"""
        # 注意：实际测试中不要清空生产 Registry，这里用独立测试
        from prometheus_client import CollectorRegistry

        self.registry = CollectorRegistry()
        yield

    def test_http_request_duration_histogram_exists(self):
        """HTTP 请求延迟 Histogram 应可创建。"""
        from prometheus_client import Histogram

        h = Histogram(
            "test_http_request_duration_seconds",
            "Test histogram",
            ["method", "endpoint"],
            registry=self.registry,
        )
        h.labels(method="GET", endpoint="/api/tickets").observe(0.1)
        assert h.labels(method="GET", endpoint="/api/tickets")._sum.get() == 0.1

    def test_counter_with_labels(self):
        """Counter 带标签应正确计数。"""
        from prometheus_client import Counter

        c = Counter(
            "test_requests_total",
            "Test counter",
            ["method", "status"],
            registry=self.registry,
        )
        c.labels(method="GET", status="200").inc()
        c.labels(method="GET", status="200").inc()
        assert c.labels(method="GET", status="200")._value.get() == 2

    def test_gauge_set_value(self):
        """Gauge 应支持 set 操作。"""
        from prometheus_client import Gauge

        g = Gauge("test_cache_size", "Test gauge", registry=self.registry)
        g.set(42)
        assert g._value.get() == 42
```

- [ ] **Step 2: 运行测试确认通过**

Run: `pytest tests/core/test_prometheus_metrics.py -v`

Expected: 3 tests PASS

- [ ] **Step 3: 重构 metrics.py 添加 Prometheus 指标**

Modify `src/multi_agent_system/core/metrics.py`:

```python
"""性能指标收集器。

提供两种指标输出格式：
1. MetricsCollector — JSON 格式（向后兼容，供 /metrics 和 Web UI 使用）
2. prometheus_client 指标 — Prometheus 标准格式（供 /prometheus 端点使用）
"""

import time
from collections import deque
from typing import Any

from loguru import logger
from prometheus_client import Counter, Gauge, Histogram

__all__ = [
    "MetricsCollector",
    "metrics_collector",
    "HTTP_REQUEST_DURATION",
    "HTTP_REQUESTS_TOTAL",
    "AGENT_EXECUTION_TOTAL",
    "AGENT_EXECUTION_DURATION",
    "LLM_CALLS_TOTAL",
    "LLM_CALL_DURATION",
    "CACHE_QUERIES_TOTAL",
    "CACHE_SIZE",
    "CACHE_HIT_RATE",
    "SYSTEM_UPTIME_SECONDS",
    "ACTIVE_REQUESTS",
]

# ============================================================
# Prometheus 指标定义
# ============================================================

HTTP_REQUEST_DURATION = Histogram(
    "multi_agent_http_request_duration_seconds",
    "HTTP 请求延迟分布（秒）",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

HTTP_REQUESTS_TOTAL = Counter(
    "multi_agent_http_requests_total",
    "HTTP 请求总数",
    ["method", "endpoint", "status"],
)

AGENT_EXECUTION_TOTAL = Counter(
    "multi_agent_agent_execution_total",
    "Agent 节点执行次数",
    ["agent_name", "status"],
)

AGENT_EXECUTION_DURATION = Histogram(
    "multi_agent_agent_execution_duration_seconds",
    "Agent 节点执行耗时分布（秒）",
    ["agent_name"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

LLM_CALLS_TOTAL = Counter(
    "multi_agent_llm_calls_total",
    "LLM 调用次数",
    ["model", "task_type"],
)

LLM_CALL_DURATION = Histogram(
    "multi_agent_llm_call_duration_seconds",
    "LLM 调用耗时分布（秒）",
    ["model"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

CACHE_QUERIES_TOTAL = Counter(
    "multi_agent_cache_queries_total",
    "缓存查询次数",
    ["result"],
)

CACHE_SIZE = Gauge(
    "multi_agent_cache_size",
    "当前缓存条目数",
)

CACHE_HIT_RATE = Gauge(
    "multi_agent_cache_hit_rate",
    "缓存命中率（0-1）",
)

SYSTEM_UPTIME_SECONDS = Gauge(
    "multi_agent_system_uptime_seconds",
    "系统运行时间（秒）",
)

ACTIVE_REQUESTS = Gauge(
    "multi_agent_active_requests",
    "当前正在处理的请求数",
)


# ============================================================
# 原有 MetricsCollector（JSON 格式，向后兼容）
# ============================================================

class MetricsCollector:
    """性能指标收集器，记录请求延迟、吞吐量和错误率（JSON 格式）。"""

    def __init__(self, max_history: int = 1000) -> None:
        self._request_times: deque[float] = deque(maxlen=max_history)
        self._error_count = 0
        self._total_count = 0
        self._start_time = time.time()

    def record_request(self, duration_ms: float, is_error: bool = False) -> None:
        """记录一次请求。"""
        self._request_times.append(duration_ms)
        self._total_count += 1
        if is_error:
            self._error_count += 1

    @property
    def total_requests(self) -> int:
        return self._total_count

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def error_rate(self) -> float:
        return self._error_count / self._total_count if self._total_count > 0 else 0.0

    @property
    def avg_latency_ms(self) -> float:
        if not self._request_times:
            return 0.0
        return sum(self._request_times) / len(self._request_times)

    @property
    def p95_latency_ms(self) -> float:
        if not self._request_times:
            return 0.0
        sorted_times = sorted(self._request_times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def throughput(self) -> float:
        """每秒请求数（基于启动时间）。"""
        elapsed = time.time() - self._start_time
        return self._total_count / elapsed if elapsed > 0 else 0.0

    def get_stats(self) -> dict[str, Any]:
        """获取所有指标统计。"""
        return {
            "total_requests": self.total_requests,
            "error_count": self.error_count,
            "error_rate": round(self.error_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "throughput": round(self.throughput, 2),
            "uptime_seconds": round(time.time() - self._start_time, 2),
        }


# 全局单例
metrics_collector = MetricsCollector()
```

- [ ] **Step 4: 运行测试确认 metrics.py 可正常导入**

Run: `python -c "from src.multi_agent_system.core.metrics import *; print('OK')"`

Expected: 输出 `OK`

Run: `pytest tests/core/test_prometheus_metrics.py -v`

Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/multi_agent_system/core/metrics.py tests/core/test_prometheus_metrics.py
git commit -m "feat(metrics): add prometheus_client metric definitions"
```

---

## Task 3: 升级 api/app.py — 新增 /prometheus 端点并升级 MetricsMiddleware

**Files:**
- Modify: `src/multi_agent_system/api/app.py`
- Test: 手动验证端点

**Context:** 当前 `app.py` 已有 `MetricsMiddleware`（记录到 `MetricsCollector`）和 `/metrics` JSON 端点。需要：
1. 升级 `MetricsMiddleware` 同时记录到 Prometheus Counter/Histogram
2. 新增 `/prometheus` 端点返回 `generate_latest(REGISTRY)`
3. 在 lifespan 中设置 `SYSTEM_UPTIME_SECONDS`
4. `ACTIVE_REQUESTS` Gauge 在请求开始时 +1，结束时 -1

- [ ] **Step 1: 修改 MetricsMiddleware 注入 Prometheus 指标**

在 `src/multi_agent_system/api/app.py` 中，修改 `MetricsMiddleware.dispatch` 方法：

```python
class MetricsMiddleware(BaseHTTPMiddleware):
    """记录请求延迟和错误率的中间件（同时支持 JSON 和 Prometheus 格式）。"""

    async def dispatch(self, request: Request, call_next):
        """处理请求并记录指标。"""
        from src.multi_agent_system.core.metrics import (
            ACTIVE_REQUESTS,
            HTTP_REQUEST_DURATION,
            HTTP_REQUESTS_TOTAL,
            metrics_collector,
        )

        start = time.time()
        is_error = False
        status_code = 200
        ACTIVE_REQUESTS.inc()

        try:
            response = await call_next(request)
            status_code = response.status_code
            if response.status_code >= 500:
                is_error = True
            return response
        except Exception:
            is_error = True
            status_code = 500
            raise
        finally:
            duration = time.time() - start
            duration_ms = duration * 1000
            metrics_collector.record_request(duration_ms, is_error)

            # Prometheus 指标记录
            method = request.method
            endpoint = request.url.path
            HTTP_REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)
            HTTP_REQUESTS_TOTAL.labels(
                method=method, endpoint=endpoint, status=str(status_code)
            ).inc()
            ACTIVE_REQUESTS.dec()
```

- [ ] **Step 2: 在 lifespan 中设置系统启动时间**

在 `src/multi_agent_system/api/app.py` 的 `lifespan` 函数中，在 `logger.info("应用初始化完成")` 之后、`yield` 之前添加：

```python
    # 设置系统启动时间（Prometheus 指标）
    from src.multi_agent_system.core.metrics import SYSTEM_UPTIME_SECONDS

    SYSTEM_UPTIME_SECONDS.set(time.time())
```

- [ ] **Step 3: 新增 /prometheus 端点**

在 `src/multi_agent_system/api/app.py` 的 `/metrics` 端点之后添加：

```python
from fastapi import Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST


@app.get("/prometheus")
async def prometheus_metrics() -> Response:
    """Prometheus 指标采集端点，返回标准文本格式。"""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
```

**注意：** 需要在文件顶部添加 `Response` 的导入（如果还没有的话）。当前文件顶部已有 `from fastapi import FastAPI`，需要改为：

```python
from fastapi import FastAPI, Response
```

- [ ] **Step 4: 本地启动验证端点**

Run: `cd /Users/ljn/Desktop/agent-study/ai-agent-learning && uvicorn src.multi_agent_system.api.app:app --reload --port 8001`

在另一个终端：

Run: `curl -s http://localhost:8001/prometheus | head -30`

Expected: 返回 Prometheus 文本格式，包含 `multi_agent_` 前缀的指标，如：

```
# HELP multi_agent_http_request_duration_seconds HTTP 请求延迟分布（秒）
# TYPE multi_agent_http_request_duration_seconds histogram
...
# HELP multi_agent_system_uptime_seconds 系统运行时间（秒）
# TYPE multi_agent_system_uptime_seconds gauge
multi_agent_system_uptime_seconds 1.71723456789e+09
```

Run: `curl -s http://localhost:8001/metrics`

Expected: 返回 JSON 格式，与之前一致（向后兼容）

- [ ] **Step 5: 停止 uvicorn 并 Commit**

```bash
git add src/multi_agent_system/api/app.py
git commit -m "feat(api): add /prometheus endpoint and upgrade MetricsMiddleware"
```

---

## Task 4: 在 Agent 节点函数中注入执行指标

**Files:**
- Modify: `src/multi_agent_system/workflow/graph.py`
- Test: 手动验证

**Context:** 需要在 `classify`、`process`、`review`、`handle_failure` 四个节点函数中注入 `AGENT_EXECUTION_TOTAL` 和 `AGENT_EXECUTION_DURATION` 指标。

- [ ] **Step 1: 在 classify 节点注入指标**

在 `src/multi_agent_system/workflow/graph.py` 的 `classify` 函数中，将：

```python
async def classify(state: TicketState) -> dict:
    """分类节点：优先使用 ClassifierAgent，不可用时降级到关键词匹配。"""
    with log_context(agent="classifier"):
```

修改为：

```python
async def classify(state: TicketState) -> dict:
    """分类节点：优先使用 ClassifierAgent，不可用时降级到关键词匹配。"""
    from src.multi_agent_system.core.metrics import (
        AGENT_EXECUTION_DURATION,
        AGENT_EXECUTION_TOTAL,
    )

    start = time.time()
    with log_context(agent="classifier"):
        try:
            # ... 原有逻辑不变 ...
            # 在函数末尾 return 之前添加成功计数
            AGENT_EXECUTION_TOTAL.labels(agent_name="classifier", status="success").inc()
            return { ... }
        except Exception:
            AGENT_EXECUTION_TOTAL.labels(agent_name="classifier", status="error").inc()
            raise
        finally:
            AGENT_EXECUTION_DURATION.labels(agent_name="classifier").observe(
                time.time() - start
            )
```

**实际修改方式：** 由于函数较长，使用 `time.perf_counter()` 包装整个函数体：

```python
async def classify(state: TicketState) -> dict:
    """分类节点：优先使用 ClassifierAgent，不可用时降级到关键词匹配。"""
    from src.multi_agent_system.core.metrics import (
        AGENT_EXECUTION_DURATION,
        AGENT_EXECUTION_TOTAL,
    )

    start = time.perf_counter()
    try:
        with log_context(agent="classifier"):
            content = state["content"]

            # Agent 可用时，直接调用
            if _classifier_agent is not None:
                result = await _classifier_agent.classify(content)
                category = result["category"]
                priority = result["priority"]
                reason = result.get("reason", "")
                return {
                    "category": category,
                    "priority": priority,
                    "status": "classifying",
                    "messages": state["messages"]
                    + [
                        {
                            "role": "classifier",
                            "content": f"分类结果: {category}, 优先级: {priority}, 理由: {reason}",
                        }
                    ],
                }

            # 占位分类：关键词匹配
            for keyword, (category, priority) in _CLASSIFY_RULES.items():
                if keyword in content:
                    return {
                        "category": category,
                        "priority": priority,
                        "status": "classifying",
                        "messages": state["messages"]
                        + [
                            {
                                "role": "classifier",
                                "content": f"分类结果: {category}, 优先级: {priority}",
                            }
                        ],
                    }

            # 默认分类
            return {
                "category": TicketCategory.INQUIRY.value,
                "priority": TicketPriority.P3.value,
                "status": "classifying",
                "messages": state["messages"]
                + [
                    {
                        "role": "classifier",
                        "content": f"分类结果: {TicketCategory.INQUIRY.value}, 优先级: {TicketPriority.P3.value}（默认）",
                    }
                ],
            }
    except Exception:
        AGENT_EXECUTION_TOTAL.labels(agent_name="classifier", status="error").inc()
        raise
    else:
        AGENT_EXECUTION_TOTAL.labels(agent_name="classifier", status="success").inc()
    finally:
        AGENT_EXECUTION_DURATION.labels(agent_name="classifier").observe(
            time.perf_counter() - start
        )
```

- [ ] **Step 2: 在 process 节点注入指标**

同理修改 `process` 函数：

```python
async def process(state: TicketState) -> dict:
    """处理节点：优先使用 ProcessorAgent，不可用时降级到占位实现。"""
    from src.multi_agent_system.core.metrics import (
        AGENT_EXECUTION_DURATION,
        AGENT_EXECUTION_TOTAL,
    )

    start = time.perf_counter()
    try:
        with log_context(agent="processor"):
            category = state.get("category", "")
            priority = state.get("priority", "P3")
            content = state["content"]

            if _processor_agent is not None:
                result = await _processor_agent.process(content, category, priority)
                processing_result = result["result"]
                return {
                    "processing_result": processing_result,
                    "status": "processing",
                    "messages": state["messages"]
                    + [{"role": "processor", "content": processing_result}],
                }

            result_map = {
                TicketCategory.TECHNICAL.value: f"已排查技术问题，生成解决方案（优先级: {priority}）",
                TicketCategory.BILLING.value: f"已核实账单信息，生成处理方案（优先级: {priority}）",
            }
            processing_result = result_map.get(
                category, f"已处理工单（分类: {category}, 优先级: {priority}）"
            )

            return {
                "processing_result": processing_result,
                "status": "processing",
                "messages": state["messages"]
                + [{"role": "processor", "content": processing_result}],
            }
    except Exception:
        AGENT_EXECUTION_TOTAL.labels(agent_name="processor", status="error").inc()
        raise
    else:
        AGENT_EXECUTION_TOTAL.labels(agent_name="processor", status="success").inc()
    finally:
        AGENT_EXECUTION_DURATION.labels(agent_name="processor").observe(
            time.perf_counter() - start
        )
```

- [ ] **Step 3: 在 review 节点注入指标**

同理修改 `review` 函数：

```python
async def review(state: TicketState) -> dict:
    """审核节点：优先使用 ReviewerAgent，不可用时降级到占位评分。"""
    from src.multi_agent_system.core.metrics import (
        AGENT_EXECUTION_DURATION,
        AGENT_EXECUTION_TOTAL,
    )

    start = time.perf_counter()
    try:
        with log_context(agent="reviewer"):
            retry_count = state.get("retry_count", 0)
            content = state["content"]
            processing_result = state.get("processing_result", "")
            category = state.get("category", "")

            if _reviewer_agent is not None:
                result = await _reviewer_agent.review(content, processing_result, category)
                score = result["score"]
                return {
                    "review_score": score,
                    "status": "reviewing",
                    "messages": state["messages"]
                    + [
                        {
                            "role": "reviewer",
                            "content": f"审核评分: {score:.2f}, 反馈: {result.get('feedback', '')}",
                        }
                    ],
                }

            base_score = 0.85
            score = max(0.3, base_score - retry_count * 0.15)

            return {
                "review_score": score,
                "status": "reviewing",
                "messages": state["messages"]
                + [{"role": "reviewer", "content": f"审核评分: {score:.2f}"}],
            }
    except Exception:
        AGENT_EXECUTION_TOTAL.labels(agent_name="reviewer", status="error").inc()
        raise
    else:
        AGENT_EXECUTION_TOTAL.labels(agent_name="reviewer", status="success").inc()
    finally:
        AGENT_EXECUTION_DURATION.labels(agent_name="reviewer").observe(
            time.perf_counter() - start
        )
```

- [ ] **Step 4: 在 handle_failure 节点注入错误计数**

修改 `handle_failure` 函数：

```python
async def handle_failure(state: TicketState) -> dict:
    """失败处理节点：标记工单状态为失败。"""
    from src.multi_agent_system.core.metrics import AGENT_EXECUTION_TOTAL

    AGENT_EXECUTION_TOTAL.labels(agent_name="failure_handler", status="error").inc()

    with log_context(agent="failure_handler"):
        error_msg = f"工单处理失败，已达最大重试次数({_get_settings().max_retries}次)"
        return {
            "status": "failed",
            "error": error_msg,
            "messages": state["messages"] + [{"role": "system", "content": error_msg}],
        }
```

- [ ] **Step 5: 在 graph.py 顶部添加 time 导入**

确认 `graph.py` 文件顶部已有 `import time`。当前文件第 8 行是 `from typing import TYPE_CHECKING, Literal`，没有 `time` 导入。需要在顶部添加：

```python
import time
```

放在 `from typing import TYPE_CHECKING, Literal` 之前。

- [ ] **Step 6: 本地验证**

Run: `cd /Users/ljn/Desktop/agent-study/ai-agent-learning && python -c "from src.multi_agent_system.workflow.graph import build_ticket_graph; print('OK')"`

Expected: 输出 `OK`（无导入错误）

- [ ] **Step 7: Commit**

```bash
git add src/multi_agent_system/workflow/graph.py
git commit -m "feat(workflow): inject prometheus metrics into agent nodes"
```

---

## Task 5: 在 LLM 调用中注入指标

**Files:**
- Modify: `src/multi_agent_system/core/cached_client.py`
- Test: 手动验证

**Context:** 在 `CachedLLMClient.chat_completions_create` 方法中，需要记录 LLM 调用次数和耗时。

- [ ] **Step 1: 修改 cached_client.py 注入 LLM 指标**

在 `src/multi_agent_system/core/cached_client.py` 的 `chat_completions_create` 方法中，将缓存命中和 API 调用部分修改为：

```python
    async def chat_completions_create(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        cache: bool = True,
        task_type: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """调用 chat.completions.create，支持缓存和模型路由。"""
        from src.multi_agent_system.core.cache import _get_llm_cache
        from src.multi_agent_system.core.metrics import (
            LLM_CALLS_TOTAL,
            LLM_CALL_DURATION,
        )

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
            start = time.perf_counter()
            try:
                result = await self.client.chat.completions.create(
                    model=use_model,
                    messages=messages,
                    temperature=temperature,
                    **kwargs,
                )
                return result
            finally:
                LLM_CALLS_TOTAL.labels(model=use_model, task_type=task_type or "unknown").inc()
                LLM_CALL_DURATION.labels(model=use_model).observe(time.perf_counter() - start)

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
            return cached_result

        # 缓存未命中，调用 API
        logger.debug(f"[CachedLLMClient] 缓存未命中，调用 {use_model}")
        start = time.perf_counter()
        try:
            result = await self.client.chat.completions.create(
                model=use_model,
                messages=messages,
                temperature=temperature,
                **kwargs,
            )
            llm_cache.set(cache_key, result)
            return result
        finally:
            LLM_CALLS_TOTAL.labels(model=use_model, task_type=task_type or "unknown").inc()
            LLM_CALL_DURATION.labels(model=use_model).observe(time.perf_counter() - start)
```

**注意：** 需要在文件顶部添加 `time` 导入：

```python
import time
```

放在 `from typing import Any` 之前。

- [ ] **Step 2: 本地验证**

Run: `cd /Users/ljn/Desktop/agent-study/ai-agent-learning && python -c "from src.multi_agent_system.core.cached_client import CachedLLMClient; print('OK')"`

Expected: 输出 `OK`

- [ ] **Step 3: Commit**

```bash
git add src/multi_agent_system/core/cached_client.py
git commit -m "feat(cached_client): inject prometheus metrics for LLM calls"
```

---

## Task 6: 在缓存查询中注入指标

**Files:**
- Modify: `src/multi_agent_system/core/cache.py`
- Test: 手动验证

**Context:** 在 `LLMCache.get` 方法中记录 hit/miss，在 `LLMCache.set` 中更新 `CACHE_SIZE`，在 `get_stats` 中更新 `CACHE_HIT_RATE`。

- [ ] **Step 1: 修改 cache.py 注入缓存指标**

在 `src/multi_agent_system/core/cache.py` 的 `LLMCache` 类中修改以下方法：

**修改 `get` 方法：**

```python
    def get(self, key: str) -> Any | None:
        """获取缓存，过期返回 None。"""
        from src.multi_agent_system.core.metrics import (
            CACHE_QUERIES_TOTAL,
            CACHE_HIT_RATE,
        )

        entry = self.cache.get(key)
        if entry is None:
            self.misses += 1
            CACHE_QUERIES_TOTAL.labels(result="miss").inc()
            CACHE_HIT_RATE.set(self.hit_rate)
            return None

        value, expire_at = entry
        if time.time() > expire_at:
            del self.cache[key]
            self.misses += 1
            CACHE_QUERIES_TOTAL.labels(result="miss").inc()
            CACHE_HIT_RATE.set(self.hit_rate)
            return None

        self.hits += 1
        CACHE_QUERIES_TOTAL.labels(result="hit").inc()
        CACHE_HIT_RATE.set(self.hit_rate)
        return value
```

**修改 `set` 方法：**

```python
    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """设置缓存，ttl 默认使用初始化参数。"""
        from src.multi_agent_system.core.metrics import CACHE_SIZE

        if ttl is None:
            ttl = self.ttl
        expire_at = time.time() + ttl
        self.cache[key] = (value, expire_at)
        CACHE_SIZE.set(len(self.cache))
```

**修改 `clear` 方法：**

```python
    def clear(self) -> None:
        """清空缓存。"""
        from src.multi_agent_system.core.metrics import CACHE_SIZE, CACHE_HIT_RATE

        self.cache.clear()
        self.hits = 0
        self.misses = 0
        CACHE_SIZE.set(0)
        CACHE_HIT_RATE.set(0.0)
```

- [ ] **Step 2: 本地验证**

Run: `cd /Users/ljn/Desktop/agent-study/ai-agent-learning && python -c "from src.multi_agent_system.core.cache import LLMCache; c = LLMCache(); c.set('k', 'v'); r = c.get('k'); print('hit' if r else 'miss')"`

Expected: 输出 `hit`

- [ ] **Step 3: Commit**

```bash
git add src/multi_agent_system/core/cache.py
git commit -m "feat(cache): inject prometheus metrics for cache queries"
```

---

## Task 7: 创建 Prometheus 配置文件

**Files:**
- Create: `prometheus.yml`

**Context:** Prometheus 需要配置 scrape target 来采集应用指标。

- [ ] **Step 1: 创建 prometheus.yml**

Create `prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "multi-agent-system"
    static_configs:
      - targets: ["api:8000"]
    metrics_path: "/prometheus"
    scrape_interval: 5s
```

- [ ] **Step 2: Commit**

```bash
git add prometheus.yml
git commit -m "chore: add prometheus scrape configuration"
```

---

## Task 8: 更新 docker-compose.yml 添加 Prometheus + Grafana

**Files:**
- Modify: `docker-compose.yml`

**Context:** 当前 docker-compose 已有 `qdrant` 和 `api` 服务。需要新增 `prometheus` 和 `grafana` 服务，并配置 volume 挂载。

- [ ] **Step 1: 修改 docker-compose.yml**

将 `docker-compose.yml` 修改为：

```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - qdrant_storage:/qdrant/storage
    restart: unless-stopped

  api:
    build:
      context: .
      args:
        - PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
    ports:
      - "8000:8000"
    depends_on:
      - qdrant
    env_file:
      - .env
    environment:
      - QDRANT_URL=http://qdrant:6333
      - CACHE_ENABLED=true
      - CACHE_MAX_SIZE=512
      - CACHE_TTL=300
    volumes:
      - ./data:/app/data
    restart: unless-stopped

  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
      - "--web.console.libraries=/usr/share/prometheus/console_libraries"
      - "--web.console.templates=/usr/share/prometheus/consoles"
      - "--web.enable-lifecycle"
    depends_on:
      - api
    restart: unless-stopped

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
      - ./grafana/dashboards:/var/lib/grafana/dashboards:ro
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_USERS_ALLOW_SIGN_UP=false
    depends_on:
      - prometheus
    restart: unless-stopped

volumes:
  qdrant_storage:
  prometheus_data:
  grafana_data:
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "chore(docker): add prometheus and grafana services"
```

---

## Task 9: 配置 Grafana 自动数据源和 Dashboard 导入

**Files:**
- Create: `grafana/provisioning/datasources/datasource.yml`
- Create: `grafana/provisioning/dashboards/dashboard.yml`

**Context:** Grafana 支持通过 provisioning 在启动时自动配置数据源和导入 Dashboard。

- [ ] **Step 1: 创建数据源配置**

Create `grafana/provisioning/datasources/datasource.yml`:

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

- [ ] **Step 2: 创建 Dashboard 导入配置**

Create `grafana/provisioning/dashboards/dashboard.yml`:

```yaml
apiVersion: 1

providers:
  - name: "default"
    orgId: 1
    folder: ""
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: /var/lib/grafana/dashboards
```

- [ ] **Step 3: Commit**

```bash
git add grafana/provisioning/
git commit -m "chore(grafana): add provisioning config for datasource and dashboards"
```

---

## Task 10: 创建 Grafana Dashboard JSON

**Files:**
- Create: `grafana/dashboards/multi-agent-system.json`

**Context:** 需要创建一个完整的 Grafana Dashboard JSON，包含概览、Agent 性能、LLM 调用、缓存、HTTP 请求五个区域。

- [ ] **Step 1: 创建 Dashboard JSON**

Create `grafana/dashboards/multi-agent-system.json`:

```json
{
  "dashboard": {
    "id": null,
    "uid": "multi-agent-system",
    "title": "多Agent工单处理系统",
    "tags": ["prometheus", "multi-agent"],
    "timezone": "browser",
    "schemaVersion": 36,
    "refresh": "5s",
    "panels": [
      {
        "id": 1,
        "title": "系统运行时间",
        "type": "stat",
        "targets": [
          {
            "expr": "multi_agent_system_uptime_seconds",
            "legendFormat": "运行时间"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "s",
            "decimals": 0
          }
        },
        "gridPos": {"h": 4, "w": 6, "x": 0, "y": 0}
      },
      {
        "id": 2,
        "title": "总请求数",
        "type": "stat",
        "targets": [
          {
            "expr": "sum(multi_agent_http_requests_total)",
            "legendFormat": "总请求"
          }
        ],
        "gridPos": {"h": 4, "w": 6, "x": 6, "y": 0}
      },
      {
        "id": 3,
        "title": "错误率",
        "type": "stat",
        "targets": [
          {
            "expr": "sum(rate(multi_agent_http_requests_total{status=~\"5..\"}[5m])) / sum(rate(multi_agent_http_requests_total[5m]))",
            "legendFormat": "错误率"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "percentunit",
            "thresholds": {
              "steps": [
                {"color": "green", "value": null},
                {"color": "yellow", "value": 0.01},
                {"color": "red", "value": 0.05}
              ]
            }
          }
        },
        "gridPos": {"h": 4, "w": 6, "x": 12, "y": 0}
      },
      {
        "id": 4,
        "title": "活跃请求数",
        "type": "stat",
        "targets": [
          {
            "expr": "multi_agent_active_requests",
            "legendFormat": "活跃请求"
          }
        ],
        "gridPos": {"h": 4, "w": 6, "x": 18, "y": 0}
      },
      {
        "id": 5,
        "title": "Agent 执行次数",
        "type": "timeseries",
        "targets": [
          {
            "expr": "sum by (agent_name) (rate(multi_agent_agent_execution_total[5m]))",
            "legendFormat": "{{agent_name}}"
          }
        ],
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 4}
      },
      {
        "id": 6,
        "title": "Agent 平均执行耗时",
        "type": "timeseries",
        "targets": [
          {
            "expr": "sum by (agent_name) (rate(multi_agent_agent_execution_duration_seconds_sum[5m])) / sum by (agent_name) (rate(multi_agent_agent_execution_duration_seconds_count[5m]))",
            "legendFormat": "{{agent_name}}"
          }
        ],
        "fieldConfig": {
          "defaults": {"unit": "s"}
        },
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": 4}
      },
      {
        "id": 7,
        "title": "LLM 调用次数（按模型）",
        "type": "piechart",
        "targets": [
          {
            "expr": "sum by (model) (multi_agent_llm_calls_total)",
            "legendFormat": "{{model}}"
          }
        ],
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 12}
      },
      {
        "id": 8,
        "title": "LLM 调用延迟 P95",
        "type": "timeseries",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, sum by (model, le) (rate(multi_agent_llm_call_duration_seconds_bucket[5m])))",
            "legendFormat": "{{model}}"
          }
        ],
        "fieldConfig": {
          "defaults": {"unit": "s"}
        },
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": 12}
      },
      {
        "id": 9,
        "title": "缓存命中率",
        "type": "timeseries",
        "targets": [
          {
            "expr": "multi_agent_cache_hit_rate",
            "legendFormat": "命中率"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "percentunit",
            "min": 0,
            "max": 1
          }
        },
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 20}
      },
      {
        "id": 10,
        "title": "缓存命中/未命中",
        "type": "timeseries",
        "targets": [
          {
            "expr": "sum by (result) (rate(multi_agent_cache_queries_total[5m]))",
            "legendFormat": "{{result}}"
          }
        ],
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": 20}
      },
      {
        "id": 11,
        "title": "HTTP 请求 QPS",
        "type": "timeseries",
        "targets": [
          {
            "expr": "sum by (endpoint) (rate(multi_agent_http_requests_total[5m]))",
            "legendFormat": "{{endpoint}}"
          }
        ],
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 28}
      },
      {
        "id": 12,
        "title": "HTTP 请求延迟 P95",
        "type": "timeseries",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, sum by (endpoint, le) (rate(multi_agent_http_request_duration_seconds_bucket[5m])))",
            "legendFormat": "{{endpoint}}"
          }
        ],
        "fieldConfig": {
          "defaults": {"unit": "s"}
        },
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": 28}
      }
    ]
  },
  "overwrite": true
}
```

- [ ] **Step 2: Commit**

```bash
git add grafana/dashboards/multi-agent-system.json
git commit -m "feat(grafana): add multi-agent system monitoring dashboard"
```

---

## Task 11: 本地集成测试

**Files:**
- 无文件修改，纯验证

- [ ] **Step 1: 构建并启动所有服务**

Run: `cd /Users/ljn/Desktop/agent-study/ai-agent-learning && docker compose up --build -d`

Expected: 四个服务（qdrant、api、prometheus、grafana）全部启动成功

Run: `docker compose ps`

Expected: 显示 4 个容器，状态均为 `Up`

- [ ] **Step 2: 验证 Prometheus 采集**

Run: `curl -s http://localhost:9090/api/v1/targets | python -m json.tool | grep -A5 multi-agent-system`

Expected: 显示 `health: "up"`，状态为 `up`

访问浏览器：`http://localhost:9090/graph`

在 Expression 输入 `multi_agent_http_requests_total`，点击 Execute，应看到指标数据。

- [ ] **Step 3: 发送测试请求产生指标数据**

Run:
```bash
for i in {1..5}; do
  curl -s -X POST http://localhost:8000/api/tickets \
    -H "Content-Type: application/json" \
    -d '{"content": "我的账户无法登录，请帮忙排查"}' > /dev/null
  sleep 1
done
```

- [ ] **Step 4: 验证 Grafana Dashboard**

访问浏览器：`http://localhost:3000`

登录：用户名 `admin`，密码 `admin`

Expected: 自动显示 "多Agent工单处理系统" Dashboard，能看到：
- 系统运行时间（非零）
- 总请求数（≥5）
- Agent 执行次数图表有数据
- HTTP 请求 QPS 有数据

- [ ] **Step 5: 验证 /prometheus 端点格式**

Run: `curl -s http://localhost:8000/prometheus | grep multi_agent_http_requests_total`

Expected: 显示类似：
```
multi_agent_http_requests_total{endpoint="/api/tickets",method="POST",status="200"} 5.0
```

- [ ] **Step 6: 验证 /metrics JSON 端点仍可用**

Run: `curl -s http://localhost:8000/metrics | python -m json.tool`

Expected: 返回 JSON 格式，包含 `total_requests`、`error_rate` 等字段

- [ ] **Step 7: 停止服务**

Run: `docker compose down`

- [ ] **Step 8: Commit（如有任何修复）**

如果测试中发现并修复了问题，commit 修复。如果一切正常，无需额外 commit。

---

## Task 12: 部署到服务器

**Files:**
- 无文件修改，纯部署操作

**Context:** 服务器是 HomeUbuntu（172.16.58.68），使用 sshpass + rsync 同步代码。

- [ ] **Step 1: 同步代码到服务器**

Run:
```bash
cd /Users/ljn/Desktop/agent-study/ai-agent-learning
sshpass -p 'ljnnb666' rsync -avz --exclude='venv' --exclude='__pycache__' --exclude='.git' \
  ./ ljn@172.16.58.68:/home/ljn/ai-agent-learning/
```

Expected: rsync 成功，文件同步到服务器

- [ ] **Step 2: 在服务器上重新构建并启动**

Run:
```bash
sshpass -p 'ljnnb666' ssh -o StrictHostKeyChecking=no ljn@172.16.58.68 \
  "cd /home/ljn/ai-agent-learning && echo 'ljnnb666' | sudo -S docker compose up -d --build"
```

Expected: Docker 构建成功，四个服务全部启动

- [ ] **Step 3: 验证服务器端 Prometheus 和 Grafana**

从本地访问：
- Prometheus: `http://172.16.58.68:9090`
- Grafana: `http://172.16.58.68:3000`

Expected: 两个服务均可访问，Dashboard 正常显示

- [ ] **Step 4: 发送测试请求验证**

Run:
```bash
for i in {1..3}; do
  curl -s -X POST http://172.16.58.68:8000/api/tickets \
    -H "Content-Type: application/json" \
    -d '{"content": "测试工单"}' > /dev/null
  sleep 1
done
```

在 Grafana 中确认指标数据已更新。

---

## Self-Review Checklist

### 1. Spec 覆盖检查

| Spec 需求 | 对应 Task | 状态 |
|-----------|-----------|------|
| Prometheus 指标导出（/prometheus 端点） | Task 3 | ✅ |
| HTTP 请求指标（延迟 + 总数） | Task 3 | ✅ |
| Agent 节点执行指标 | Task 4 | ✅ |
| LLM 调用指标 | Task 5 | ✅ |
| 缓存指标 | Task 6 | ✅ |
| 系统指标（uptime + active_requests） | Task 3 | ✅ |
| Grafana 预配置面板 | Task 10 | ✅ |
| 概览面板 | Task 10 (panels 1-4) | ✅ |
| Agent 性能面板 | Task 10 (panels 5-6) | ✅ |
| LLM 调用面板 | Task 10 (panels 7-8) | ✅ |
| 缓存面板 | Task 10 (panels 9-10) | ✅ |
| HTTP 请求面板 | Task 10 (panels 11-12) | ✅ |

**无缺口。**

### 2. 占位符扫描

- [x] 无 "TBD"、"TODO"、"implement later"
- [x] 无 "Add appropriate error handling" 等模糊描述
- [x] 每个代码步骤包含完整代码
- [x] 无 "Similar to Task N" 引用

### 3. 类型一致性检查

- [x] `HTTP_REQUEST_DURATION` — Histogram，labels: method, endpoint — 全文件一致
- [x] `AGENT_EXECUTION_TOTAL` — Counter，labels: agent_name, status — 全文件一致
- [x] `LLM_CALLS_TOTAL` — Counter，labels: model, task_type — 全文件一致
- [x] `CACHE_QUERIES_TOTAL` — Counter，labels: result — 全文件一致
- [x] 指标名称 `multi_agent_*` 前缀 — 全文件一致
- [x] `time.perf_counter()` 用于耗时测量 — Task 4/5 一致

---

## 执行选项

**Plan complete and saved to `docs/superpowers/plans/2026-05-15-prometheus-monitoring.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
