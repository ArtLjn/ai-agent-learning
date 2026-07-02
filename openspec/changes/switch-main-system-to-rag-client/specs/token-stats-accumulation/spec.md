## ADDED Requirements

### Requirement: Token 累加修复（P0）

系统 SHALL 在 `SpanContext.__aexit__`（即 span 关闭阶段）调用 `TraceManager.add_token_usage`，将 LLM 调用消耗的 token 累加到 `traces.total_tokens` 字段。

修复 v1.1 P0 bug：`traces.total_tokens` 永远为 0（`add_token_usage` 方法存在但未被调用）。

#### Scenario: llm_call span 关闭时累加 token

- **WHEN** 一个 `span_type=llm_call` 的 span 关闭且 `output_data.tokens` 非空
- **THEN** 系统 MUST 调用 `add_token_usage(trace_id, delta=total_tokens)`
- **AND** `traces.total_tokens` MUST 实际累加（不再恒为 0）
- **AND** 多个 llm_call span 的 token MUST 正确汇总

#### Scenario: 非 llm_call span 不触发累加

- **WHEN** 一个 `span_type=node` 或 `span_type=tool_call` 的 span 关闭
- **THEN** 系统 MUST 不调用 `add_token_usage`
- **AND** `traces.total_tokens` MUST 不被这些 span 影响

#### Scenario: tokens 字段缺失时不累加

- **WHEN** llm_call span 的 `output_data.tokens` 为空或缺失
- **THEN** 系统 MUST 跳过累加
- **AND** 不抛出异常

### Requirement: token_daily_stats 表写入

系统 SHALL 在 `SpanContext.__aexit__` 累加 token 后，同步写入 `token_daily_stats` 表（按 user_id + date + model_name 聚合），为开发人员模块 Token 成本控制台提供数据。

`token_daily_stats` 表 schema MUST 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | 自增主键 |
| `user_id` | TEXT | 用户 ID |
| `date` | DATE | 聚合日期 |
| `model_name` | TEXT | LLM 模型名 |
| `prompt_tokens` | INT | 输入 token |
| `completion_tokens` | INT | 输出 token |
| `total_tokens` | INT | 总 token |
| `ticket_id` | TEXT | 关联工单（可选） |
| `trace_id` | TEXT | 关联 trace |
| `created_at` | TIMESTAMP | 写入时间 |

#### Scenario: token 日聚合写入

- **WHEN** 一个 llm_call span 关闭且产生 150 token
- **THEN** 系统 MUST 在 `token_daily_stats` 表写入（或更新）一行
- **AND** 行的 `user_id` MUST 来自 span 上下文或 trace 上下文
- **AND** 行的 `date` MUST 为当前日期
- **AND** 行的 `model_name` MUST 来自 span output_data

#### Scenario: 同 user + date + model 多次累加

- **WHEN** 同一 user 同一天使用同一 model 多次调用 LLM
- **THEN** `token_daily_stats` 表 MUST 通过 UPDATE 累加（而非每次 INSERT 新行）
- **AND** 聚合行的 `total_tokens` MUST 反映累计值

### Requirement: Token 模型字段适配（去 farm_id 加 ticket_id）

系统 SHALL 提供 `models/token_stats.py` 中的 Pydantic 模型，从 farm-manager 项目复用但适配本系统：

- 移除 `farm_id` 字段（本系统无农场概念）
- 新增 `ticket_id` 字段（关联工单，可选）
- 保留 `user_id`、`model_name`、`prompt_tokens`、`completion_tokens`、`total_tokens`、`date`

#### Scenario: TokenDailyStats 模型字段

- **WHEN** 查询接口返回 `TokenDailyStats` 对象
- **THEN** 对象 MUST 包含 `user_id`、`date`、`model_name`、`prompt_tokens`、`completion_tokens`、`total_tokens`、`ticket_id` 字段
- **AND** MUST 不包含 `farm_id` 字段

#### Scenario: 按工单维度查询 token

- **WHEN** 调用 `GET /admin/tokens?ticket_id=TK-xxx`
- **THEN** 系统 MUST 返回该工单所有 LLM 调用的 token 记录
- **AND** 便于分析「这个工单花了多少 token」

### Requirement: 配额服务（软提醒）

系统 SHALL 提供 `services/quota_service.py` 的 `QuotaService` 类，检查用户月/周配额使用情况，返回 `QuotaInfo`。

`QuotaInfo` 字段 MUST 包含：`monthly_used`、`monthly_limit`、`weekly_used`、`weekly_limit`、`status`（`ok` / `warning` / `exceeded`）。

`status` 判定规则：

- 使用量 < 80% → `ok`
- 80% ≤ 使用量 ≤ 100% → `warning`
- 使用量 > 100% → `exceeded`

配额检查 MUST 为软提醒，不阻断 LLM 调用。

#### Scenario: 配额正常

- **WHEN** 用户本月已用 50000 token，月配额 100000
- **THEN** `QuotaService.check(user_id)` MUST 返回 `{status: "ok", monthly_used: 50000, monthly_limit: 100000, ...}`
- **AND** 系统 MUST 不阻断后续 LLM 调用

#### Scenario: 配额预警

- **WHEN** 用户本月已用 85000 token（85%），月配额 100000
- **THEN** `QuotaService.check(user_id)` MUST 返回 `{status: "warning", ...}`
- **AND** 系统 MUST 仅推送预警事件给开发人员模块前端
- **AND** 系统 MUST 不阻断 LLM 调用

#### Scenario: 配额超限（软提醒）

- **WHEN** 用户本月已用 110000 token（110%），月配额 100000
- **THEN** `QuotaService.check(user_id)` MUST 返回 `{status: "exceeded", ...}`
- **AND** 系统 MUST 仅推送超限事件
- **AND** 系统 MUST 不阻断 LLM 调用（毕设范围软提醒模式）

### Requirement: users 表配额字段

系统 SHALL 在 `users` 表新增两个字段持久化配额配置：

- `token_monthly_limit`：INT，默认 100000
- `token_weekly_limit`：INT，默认 25000

字段可通过管理员 API `PUT /admin/quota/{user_id}` 调整。

#### Scenario: 默认配额生效

- **WHEN** 新用户注册且未配置配额
- **THEN** `users.token_monthly_limit` MUST 默认为 100000
- **AND** `users.token_weekly_limit` MUST 默认为 25000

#### Scenario: 管理员调整配额

- **WHEN** 管理员调用 `PUT /admin/quota/{user_id}` 设置 `monthly_limit=200000`
- **THEN** `users.token_monthly_limit` MUST 更新为 200000
- **AND** 下次 `QuotaService.check(user_id)` MUST 基于新配额计算 status

### Requirement: Token 查询 API

系统 SHALL 在 `api/admin_stats.py` 提供 4 个开发人员模块 Token 端点：

- `GET /admin/tokens`：按 user_id + date 范围 + model_name 查询 token_daily_stats
- `GET /admin/tokens/daily`：返回最近 30 天的按日聚合 token 消耗
- `GET /admin/tokens/hourly`：返回最近 24 小时的按小时聚合
- `GET /admin/quota/{user_id}`：返回该用户的 `QuotaInfo`

所有端点 MUST 要求管理员权限（403 `FORBIDDEN_NOT_ADMIN` 若非管理员）。

#### Scenario: 查询日聚合

- **WHEN** 管理员调用 `GET /admin/tokens/daily?user_id=U1`
- **THEN** 系统 MUST 返回该用户最近 30 天每日的 token 总量
- **AND** 每项 MUST 包含 `date`、`total_tokens`、`prompt_tokens`、`completion_tokens`

#### Scenario: 非管理员访问被拒

- **WHEN** 非管理员用户调用 `GET /admin/tokens/daily`
- **THEN** 系统 MUST 返回 `403 FORBIDDEN_NOT_ADMIN`
- **AND** 不返回任何 token 数据

#### Scenario: 用户不存在

- **WHEN** 管理员调用 `GET /admin/quota/U-unknown`
- **THEN** 系统 MUST 返回 `404 USER_NOT_FOUND`
