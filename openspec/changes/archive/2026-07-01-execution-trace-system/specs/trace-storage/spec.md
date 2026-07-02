## ADDED Requirements

### Requirement: traces 表持久化
系统 SHALL 在 SQLite 中创建 `traces` 表，存储每个工单处理的顶层追踪记录。

表结构：
- `trace_id TEXT PRIMARY KEY` — UUID 格式
- `ticket_id TEXT NOT NULL` — 关联工单 ID
- `status TEXT NOT NULL DEFAULT 'running'` — running/completed/failed
- `start_time REAL NOT NULL` — Unix 时间戳
- `end_time REAL` — Unix 时间戳
- `duration REAL` — 秒
- `total_tokens INTEGER DEFAULT 0` — LLM 总 token 消耗
- `total_tool_calls INTEGER DEFAULT 0` — 工具调用总次数
- `node_count INTEGER DEFAULT 0` — 执行节点数
- `error TEXT` — 失败原因

#### Scenario: 创建 trace 记录
- **WHEN** 工单开始处理
- **THEN** 系统创建一条 traces 记录，status='running'，记录 start_time

#### Scenario: 完成 trace 记录
- **WHEN** 工单处理完成（completed 或 failed）
- **THEN** 系统更新 traces 记录的 status、end_time、duration、total_tokens、total_tool_calls、node_count

### Requirement: spans 表持久化
系统 SHALL 在 SQLite 中创建 `spans` 表，存储 trace 内的每个执行单元。

表结构：
- `span_id TEXT PRIMARY KEY` — UUID 格式
- `trace_id TEXT NOT NULL` — 关联 trace
- `parent_span_id TEXT` — 父 span（支持嵌套）
- `span_type TEXT NOT NULL` — node/react_iter/llm_call/tool_call
- `name TEXT NOT NULL` — 节点名或工具名
- `status TEXT NOT NULL DEFAULT 'ok'` — ok/error/fallback
- `input_data TEXT` — JSON 格式输入快照
- `output_data TEXT` — JSON 格式输出快照
- `start_time REAL NOT NULL` — Unix 时间戳
- `end_time REAL` — Unix 时间戳
- `duration REAL` — 秒
- `metadata TEXT` — JSON 格式扩展信息（model、tokens、error_msg 等）

索引：
- `idx_spans_trace` ON spans(trace_id)
- `idx_spans_parent` ON spans(parent_span_id)
- `idx_spans_type` ON spans(span_type)

#### Scenario: 创建 span 记录
- **WHEN** 节点/LLM/工具开始执行
- **THEN** 系统创建一条 spans 记录，记录 span_id、trace_id、parent_span_id、span_type、name、start_time

#### Scenario: 嵌套 span
- **WHEN** ReAct 循环中执行 LLM 调用
- **THEN** 创建的 llm_call span 的 parent_span_id 指向所属的 react_iter span

#### Scenario: 完成 span
- **WHEN** 执行单元结束
- **THEN** 系统更新 end_time、duration、status，可选更新 output_data 和 metadata

### Requirement: trace 聚合查询
系统 SHALL 支持以下聚合查询：
- 按 ticket_id 查询完整 trace（含所有 span）
- 按时间范围查询 trace 列表（支持 status 过滤）
- 按 span_type 聚合平均耗时
- 查询指定 trace 的最长耗时 span（瓶颈定位）

#### Scenario: 查询工单 trace
- **WHEN** 调用 get_trace_by_ticket(ticket_id)
- **THEN** 返回 trace 记录及其所有 span（按 start_time 排序）

#### Scenario: 按类型聚合耗时
- **WHEN** 调用 get_span_duration_stats(trace_id, span_type="tool_call")
- **THEN** 返回该 trace 下所有 tool_call span 的平均耗时、最大耗时、调用次数
