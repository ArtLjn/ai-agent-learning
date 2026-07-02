## ADDED Requirements

### Requirement: 开发人员通过工单 ID 查询完整执行树

系统 SHALL 提供 `GET /api/admin/traces/{ticket_id}` 接口，返回该工单对应的完整 span 树（含父子关系、span_type、duration、metadata）。

- 路径参数 `ticket_id` MUST 为已存在工单，否则返回 404
- 响应 MUST 包含 `trace_id`、`ticket_id`、`status`、`total_tokens`、`duration`、`spans[]`
- 每个 span MUST 包含 `span_id`、`parent_span_id`、`span_type`、`name`、`status`、`duration`、`metadata`（含 `decision` / `token_usage` / `rag_stats` 子结构）
- 接口 MUST 通过 `require_admin` 鉴权

#### Scenario: 查询存在的工单 trace

- **WHEN** 开发人员调用 `GET /api/admin/traces/TK-20260701-001`
- **THEN** 系统 MUST 返回 200
- **AND** 响应 `spans` 数组 MUST 按 `start_time` 升序排列
- **AND** 父子关系通过 `parent_span_id` 链接成树

#### Scenario: 查询不存在工单

- **WHEN** 开发人员调用 `GET /api/admin/traces/TK-NOTEXIST`
- **THEN** 系统 MUST 返回 404

#### Scenario: 非管理员访问

- **WHEN** 普通用户或未登录用户调用该接口
- **THEN** 系统 MUST 返回 403

### Requirement: 抽取 trace 内决策点列表

系统 SHALL 提供 `GET /api/admin/traces/{ticket_id}/decisions` 接口，从所有 span 的 `metadata.decision` 子结构中抽取决策点摘要。

- 响应 MUST 包含 `trace_id`、`ticket_id`、`decision_count`、`decisions[]`
- 每个 decision 摘要 MUST 包含 `span_id`、`span_name`、`decision_type`、`selection_value`、`confidence`、`options_count`、`timestamp`
- 仅返回包含 `metadata.decision` 字段的 span，无决策字段的执行 span（如 memory_call）不返回
- `decision_type` 枚举 MUST 为：`routing` / `branching` / `tool_selection` / `quality_gate` / `boundary` / `escalation`

#### Scenario: 抽取多决策点 trace

- **WHEN** 开发人员查询一条含 classify / route / review / retry_check 4 个决策点的工单
- **THEN** 响应 `decision_count` MUST 为 4
- **AND** `decisions[]` MUST 按 `timestamp` 升序排列
- **AND** 每项 `selection_value` 与 `confidence` 来自原 `metadata.decision.selection`

#### Scenario: 无决策点的 trace

- **WHEN** 开发人员查询一条因异常 fallback 而无决策 span 的工单
- **THEN** 响应 `decision_count` MUST 为 0
- **AND** `decisions[]` 为空数组（不抛异常）

### Requirement: 前端 SpanTreeView 渲染决策点五元组

前端 `web/src/pages/dev/SpanTreeView.tsx` SHALL 复用 `web/src/components/trace/` 现有组件渲染工单执行树，并对含 `metadata.decision` 的 span 加决策类型徽章。

- 决策类型徽章颜色规则：routing 蓝、branching 紫、quality_gate 青、boundary 橙、tool_selection 灰、escalation 红
- 置信度色阶：< 0.5 红、[0.5, 0.7) 橙、[0.7, 0.9) 黄、≥ 0.9 绿
- 点击 span 节点 MUST 弹出 `SpanDetailSheet` 展示完整 metadata（含五元组）
- 支持 URL query 参数 `?ticket_id=...&span_id=...` 自动定位到指定 span 并展开

#### Scenario: 渲染决策树

- **WHEN** 开发人员打开 `/dev/traces?ticket_id=TK-20260701-001`
- **THEN** SpanTreeView MUST 调用 `GET /api/admin/traces/TK-20260701-001`
- **AND** 渲染 span 树，决策 span 显示对应颜色徽章
- **AND** 右侧 DecisionTimeline 列出所有决策点

#### Scenario: 从 Token 控制台跳转定位

- **WHEN** URL 为 `/dev/traces?ticket_id=TK-X&span_id=span-Y`
- **THEN** SpanTreeView MUST 自动滚动到 span-Y
- **AND** SpanDetailSheet MUST 自动展开

### Requirement: span 详情接口

系统 SHALL 提供 `GET /api/admin/traces/{ticket_id}/spans/{span_id}` 接口返回单个 span 的完整数据。

- 响应 MUST 包含 span 全部字段（含 input_data / output_data / metadata）
- `metadata` 中如有 `decision` MUST 完整返回（trigger / options / selection / execution / reflection）
- `metadata` 中如有 `token_usage` MUST 返回 prompt_tokens / completion_tokens / total_tokens / model
- `metadata` 中如有 `rag_stats` MUST 返回 hit_count / top_score / mode

#### Scenario: 查询 LLM span 的 token 明细

- **WHEN** 开发人员查询 `span_type=llm_call` 的 span
- **THEN** 响应 `metadata.token_usage` MUST 包含 4 个 token 字段
- **AND** 可用于论文成本数据取数
