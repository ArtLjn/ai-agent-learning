## Context

当前多 Agent 工单系统已完成 ReAct 推理、分层记忆、工具 Schema 校验等核心架构改造。工单通过 LangGraph 管道处理：receive → classify → route → process(ReAct) → review → notify → complete，每个节点可能包含 LLM 调用和工具调用。但系统缺少执行过程的可观测性——无法回溯一个工单处理了多久、哪步是瓶颈、工具调用是否有效、降级发生在哪。

现有基础设施：
- **DatabaseManager**（`core/database.py`）：SQLite 异步连接管理，已有 tickets/users/checkpoints/patterns 四张表
- **MemoryManager**（`core/memory.py`）：工作记忆的 thought_chain 和 tool_history 已在 ReAct 循环中记录，但仅内存保留，不持久化
- **MetricsCollector**（`core/metrics.py`）：Prometheus 指标，记录 Agent 执行次数和耗时，但只有聚合指标，没有单次执行链路
- **WebSocket**（`api/routes.py`）：已有 `/ws/tickets/{id}` 推送节点完成事件

## Goals / Non-Goals

**Goals:**
- 为每个工单处理创建完整的执行 Trace，记录所有节点、LLM 调用、工具调用的耗时和输入输出
- 采用 OpenTelemetry 风格的 Trace/Span 嵌套模型，支持父子关系
- 提供 REST API 查询执行链路，WebSocket 实时推送 span 事件
- 与现有 MemoryManager 的工作记忆打通（thought_chain/tool_history 可从 trace 重建）

**Non-Goals:**
- 不做分布式追踪（单进程 SQLite 场景，无需跨服务 trace context propagation）
- 不引入 OpenTelemetry SDK 依赖（自己实现轻量版，面试更容易讲清楚原理）
- 不做 trace 数据的自动清理策略（后续迭代加 TTL）
- 不改变现有 LangGraph 节点逻辑（纯增量包裹）

## Decisions

### 1. 数据模型：两表 Trace + Span

**选择**：SQLite 两表设计（traces + spans），而非单表 JSON 或纯文件。

**替代方案**：
- 单表 + JSON 字段存储 span 树：查询灵活但嵌套 JSON 难以索引
- 纯文件（JSONL）：简单但无法查询和聚合
- 引入 Jaeger/Zipkin：过度工程化，本地 SQLite 场景不需要

**理由**：两表设计支持 span 的父子关系查询、按类型/状态过滤、耗时聚合统计。SQLite 写入 <1ms，不影响主流程。

### 2. Span 嵌套模型

**选择**：每个 span 有 `parent_span_id`，形成树状结构。

**层级设计**：
```
Trace (ticket_id)
├── Span[node] receive
├── Span[node] classify
│   └── Span[llm_call] (parent=classify)
├── Span[node] process
│   ├── Span[react_iter] (parent=process, iteration=1)
│   │   ├── Span[llm_call] (parent=react_iter#1)
│   │   └── Span[tool_call] (parent=react_iter#1)
│   └── Span[react_iter] (parent=process, iteration=2)
│       └── ...
├── Span[node] review
└── Span[node] complete
```

**span_type 枚举**：`node`、`react_iter`、`llm_call`、`tool_call`

**替代方案**：扁平列表（无父子关系）——无法表达"这个 LLM 调用属于哪个 ReAct 迭代"。

### 3. 集成方式：Context Manager + 装饰器

**选择**：用 Python async context manager 包裹执行逻辑，自动记录 span 的开始和结束时间。

```python
async with tracer.start_span("classify", span_type="node") as span:
    # 业务逻辑
    result = await agent.classify(content)
    span.set_output({"category": result["category"]})
```

**替代方案**：
- 装饰器 `@trace_span("name")`：不够灵活，无法在函数内部添加子 span
- 回调函数：侵入性太强，需要修改每个函数签名
- LangGraph callback：LangGraph 的 callback 机制过于复杂，且不支持自定义 span 嵌套

### 4. 与 MemoryManager 的关系

**选择**：Trace 系统独立于 MemoryManager，但 ReAct 循环中两者并行记录。

- **MemoryManager**：关注业务语义（推理链、工具结果、用户上下文）
- **TraceManager**：关注执行可观测性（耗时、状态、性能分析）

MemoryManager 的 `thought_chain` 和 `tool_history` 仍保留（用于 ReAct 上下文拼接），Trace 系统不替代它们。

### 5. WebSocket 集成

**选择**：复用现有 `_broadcast_ticket_update` 机制，在 span 完成时推送 `span_complete` 事件。

```json
{
  "type": "span_complete",
  "span_id": "...",
  "span_type": "node",
  "name": "classify",
  "duration": 0.115,
  "status": "ok"
}
```

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| SQLite 写入增加 I/O 延迟 | span 记录为单行 INSERT，<1ms；异步执行不阻塞主流程 |
| span 数据量增长 | 单个工单约 10-30 个 span（几 KB），短期不会成为问题 |
| 与 MemoryManager 职责重叠 | 明确边界：Memory 管"语义"，Trace 管"可观测性" |
| graph.py 修改影响现有流程 | 只在节点函数开头/结尾加 span 包裹，不改变节点逻辑 |
