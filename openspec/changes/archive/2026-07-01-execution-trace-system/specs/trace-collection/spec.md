## ADDED Requirements

### Requirement: TraceManager 生命周期管理
系统 SHALL 提供 `TraceManager` 类，管理 trace 的创建、完成和失败。

- `start_trace(ticket_id: str) -> str` — 创建 trace，返回 trace_id
- `finish_trace(trace_id: str, status: str, error: str | None = None)` — 完成 trace，自动计算 duration
- `fail_trace(trace_id: str, error: str)` — 标记 trace 为 failed

TraceManager MUST 通过构造函数接收 `DatabaseManager` 实例。

#### Scenario: 工单处理开始
- **WHEN** LangGraph 工作流开始执行
- **THEN** 调用 start_trace(ticket_id) 创建 trace 记录，返回 trace_id

#### Scenario: 工单处理完成
- **WHEN** 工单到达 complete 节点
- **THEN** 调用 finish_trace(trace_id, "completed")，自动设置 end_time 和 duration

#### Scenario: 工单处理失败
- **WHEN** 工单到达 handle_failure 节点
- **THEN** 调用 finish_trace(trace_id, "failed", error=error_msg)

### Requirement: Span 创建与嵌套
系统 SHALL 通过 async context manager 支持嵌套 span 创建。

```python
async with tracer.start_span("classify", span_type="node") as span:
    async with tracer.start_span("llm_call", span_type="llm_call", parent=span) as llm_span:
        ...
```

- `start_span(name, span_type, parent=None, input_data=None) -> SpanContext` — 返回 async context manager
- `SpanContext` MUST 提供：`span_id`、`set_output(data)`、`set_metadata(data)`、`set_status(status)`

#### Scenario: 节点 span 包裹
- **WHEN** LangGraph 节点函数执行
- **THEN** 通过 `async with tracer.start_span(node_name, "node")` 包裹，自动记录 start_time 和 end_time

#### Scenario: ReAct 迭代嵌套
- **WHEN** ReAct 循环每轮执行
- **THEN** 创建 react_iter span（parent=process node span），内部可嵌套 llm_call 和 tool_call span

#### Scenario: span 异常捕获
- **WHEN** span 内部代码抛出异常
- **THEN** span 自动标记 status='error'，记录 error 到 metadata，异常继续向上传播

### Requirement: 与 LangGraph 节点集成
系统 MUST 在 `graph.py` 的每个节点函数中包裹 span，不改变节点原有逻辑。

包裹方式：
- `receive`、`classify`、`process`、`review`、`notify`、`complete`、`handle_failure`、`auto_reply`、`escalate` 均需包裹
- 节点 span 的 input_data 记录关键 state 字段（content、category、priority）
- 节点 span 的 output_data 记录节点产出（如 category、processing_result、review_score）

#### Scenario: classify 节点追踪
- **WHEN** classify 节点执行
- **THEN** 创建 span(name="classify", type="node")，input_data 包含 content，output_data 包含 category 和 priority

#### Scenario: process 节点追踪（含 ReAct）
- **WHEN** process 节点执行
- **THEN** 创建 span(name="process", type="node")，内部包含多个 react_iter 子 span

### Requirement: 与 ReAct 循环集成
系统 MUST 在 `processor_react.py` 的 ReAct 循环中记录每轮迭代。

每轮迭代记录：
- `react_iter` span：parent 指向 process node span，metadata 包含 iteration 序号
- 内部嵌套 `llm_call` span：记录 model、tokens、duration
- 内部嵌套 `tool_call` span（如有）：记录 tool_name、params、result

#### Scenario: ReAct 单轮追踪
- **WHEN** ReAct 循环第 N 轮执行
- **THEN** 创建 react_iter span，内部包含一个 llm_call span 和可选的 tool_call span

#### Scenario: ReAct 工具调用追踪
- **WHEN** ReAct 循环中执行工具
- **THEN** 创建 tool_call span，input_data 包含工具参数，output_data 包含工具返回值，metadata 包含 tool_name

### Requirement: 与 LLM 调用层集成
系统 MUST 在 `cached_client.py` 的 `chat_completions_create` 方法中记录 LLM 调用 span。

记录内容：
- span_type="llm_call"
- name="chat_completions"
- metadata: model、task_type、prompt_tokens、completion_tokens
- 通过 TracerAccessor 获取当前活跃 trace

#### Scenario: LLM 调用追踪
- **WHEN** 调用 chat_completions_create
- **THEN** 自动创建 llm_call span，记录 model、tokens、duration
