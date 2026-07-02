## ADDED Requirements

### Requirement: Agent 调用聚合统计接口

系统 SHALL 提供 `GET /api/admin/stats/agents` 接口，按 call_type 聚合 spans 表，返回 5 Agent 的调用次数、平均耗时、成功率、错误率、总 token。

查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `start_date` | date | 是 | 起始日期（含） |
| `end_date` | date | 是 | 结束日期（含） |
| `agent_name` | string | 否 | 过滤特定 Agent |

响应 `agents[]` 数组每项 MUST 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `agent_name` | string | 5 Agent 之一 |
| `call_count` | int | 时间区间内 span 数量 |
| `avg_duration_ms` | float | 平均耗时（毫秒） |
| `success_rate` | float | 0.0-1.0，status=success 占比 |
| `error_rate` | float | 0.0-1.0，status=error 占比 |
| `total_tokens` | int | 累计 token（来自 spans.metadata.token_usage） |

call_type 与 agent_name 映射 MUST 对齐 [12 号文档第 7 节枚举](../../../docs/design-spec/01_正式设计/12_Token成本控制台设计.md)：intent / classify / process / review / coordinator / rag。

#### Scenario: 查询近 7 天统计

- **WHEN** 开发人员调用 `?start_date=2026-06-25&end_date=2026-07-01`
- **THEN** 响应 `agents[]` MUST 包含 5 个 Agent 的聚合数据
- **AND** `call_count` 为该区间内每个 Agent 对应 call_type 的 span 总数

#### Scenario: 过滤特定 Agent

- **WHEN** 开发人员调用 `?agent_name=classifier`
- **THEN** 响应仅含 classifier 一项

### Requirement: 成功率与错误率计算

`success_rate` 与 `error_rate` MUST 基于 span 的 `status` 字段计算：

- `success_rate` = `status=success` 的 span 数 / 总 span 数
- `error_rate` = `status=error` 的 span 数 / 总 span 数
- 两者之和不必为 1（存在 `status=running` 等中间态）

#### Scenario: 全部成功的 Agent

- **WHEN** classifier 在查询区间内 100 次 span 全部 status=success
- **THEN** `success_rate` MUST 为 1.0
- **AND** `error_rate` MUST 为 0.0

#### Scenario: 部分失败

- **WHEN** react_processor 在区间内 50 次成功、10 次失败、5 次运行中
- **THEN** `success_rate` MUST 为 50/65 ≈ 0.769
- **AND** `error_rate` MUST 为 10/65 ≈ 0.154

### Requirement: token 聚合来自 span metadata

`total_tokens` MUST 来自该 Agent 对应 call_type 的所有 span 的 `metadata.token_usage.total_tokens` 之和，**不**读 `traces.total_tokens`（避免跨 Agent 重复计）。

- 仅统计 `span_type=llm_call` 且 `metadata.token_usage` 存在的 span
- 缺失 `token_usage` 字段的 span 按 0 计入 token，但 `call_count` 仍 +1

#### Scenario: 累加 token

- **WHEN** classifier 在区间内有 3 个 llm_call span，token_usage.total_tokens 分别为 200 / 250 / 180
- **THEN** `total_tokens` MUST 为 630

#### Scenario: 缺失 token_usage 字段

- **WHEN** 某 span 是 llm_call 但 metadata.token_usage 为空（埋点缺失）
- **THEN** 该 span 仍计入 `call_count`
- **AND** 对 `total_tokens` 贡献 0

### Requirement: 前端 AgentCallStats 三视图切换

前端 `web/src/pages/dev/AgentCallStats.tsx` SHALL 提供时间范围选择器 + 表格 + 三视图切换的柱状图。

- 顶部：日期范围选择（默认近 7 天）+ Agent 多选过滤
- 表格：5 Agent 一行，列含调用次数 / 平均耗时 / 成功率 / 错误率 / 总 token
- 柱状图（Recharts）：三视图切换按钮——「调用次数」「平均耗时」「成功率」
- 成功率列 MUST 用色阶：< 0.8 红、[0.8, 0.95) 橙、≥ 0.95 绿

#### Scenario: 切换柱状图视图

- **WHEN** 开发人员点击「平均耗时」视图按钮
- **THEN** 柱状图 MUST 重新渲染，Y 轴改为 ms，每个柱代表一个 Agent 的 avg_duration_ms

#### Scenario: 时间范围变更

- **WHEN** 开发人员选择「近 30 天」
- **THEN** 表格与柱状图 MUST 重新查询并刷新
- **AND** 显示 loading 态
