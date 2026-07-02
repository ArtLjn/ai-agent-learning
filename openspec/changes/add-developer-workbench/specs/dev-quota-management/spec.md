## ADDED Requirements

### Requirement: per-user 配额覆写接口

系统 SHALL 提供 `PATCH /api/admin/users/{user_id}/quota` 接口，允许管理员为单个用户覆写月配额与周配额。

请求体 MUST 支持：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `token_monthly_limit` | int / null | 否 | 月配额（token），null 表示走系统默认 |
| `token_weekly_limit` | int / null | 否 | 周配额（token），null 表示走系统默认 |

接口 MUST 通过 `require_admin` 鉴权，并在响应中返回更新后的用户配额摘要。

#### Scenario: 覆写月配额

- **WHEN** 管理员调用 `PATCH /api/admin/users/U-123/quota`，body=`{token_monthly_limit: 500000}`
- **THEN** 系统 MUST 更新 `users.token_monthly_limit=500000` 对应行
- **AND** 响应返回 `{user_id, token_monthly_limit: 500000, token_weekly_limit: <previous>}`

#### Scenario: 清除覆写走默认

- **WHEN** 管理员调用 body=`{token_monthly_limit: null}`
- **THEN** 系统 MUST 将 `users.token_monthly_limit` 设为 NULL
- **AND** 该用户后续配额检查走 `config.token_quota.monthly_limit` 默认值

#### Scenario: 非 admin 访问

- **WHEN** 普通用户调用该接口
- **THEN** 系统 MUST 返回 403

### Requirement: 配额优先级

配额检查时的优先级 MUST 为：`users.token_monthly_limit` > `config.token_quota.monthly_limit`。

- 当 `users.token_monthly_limit IS NOT NULL` 时，使用该值
- 当 `users.token_monthly_limit IS NULL` 时，回退到 config 默认值
- 周配额同理

#### Scenario: 用户级覆写优先

- **WHEN** 用户 U-123 的 `token_monthly_limit=500000`，config 默认 `monthly_limit=1000000`
- **THEN** 该用户月配额 MUST 按 500000 检查
- **AND** 不受 config 默认值影响

#### Scenario: NULL 走默认

- **WHEN** 用户 U-456 的 `token_monthly_limit=NULL`，config 默认 1000000
- **THEN** 该用户月配额 MUST 按 1000000 检查

### Requirement: 配额查询接口

系统 SHALL 提供 `GET /api/admin/stats/quota/{user_id}` 接口（与 [12 号文档 4.3 节](../../../docs/design-spec/01_正式设计/12_Token成本控制台设计.md) 定义对齐），返回用户当前配额使用情况。

响应 MUST 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `user_id` | string | 用户 ID |
| `monthly_usage` | int | 当月已用 token |
| `monthly_limit` | int | 当月配额（取 user 或 config 优先级） |
| `monthly_remaining` | int | 月剩余 |
| `weekly_usage` | int | 本周已用 |
| `weekly_limit` | int | 本周配额 |
| `weekly_remaining` | int | 周剩余 |
| `reset_at` | datetime | 配额重置时间（月/周重置点） |

#### Scenario: 查询用户配额

- **WHEN** 管理员调用 `GET /api/admin/stats/quota/U-123`
- **THEN** 系统 MUST 调用 `quota_service.check_user_quota(U-123)`
- **AND** 返回完整 8 字段

#### Scenario: 用户不存在

- **WHEN** 管理员查询不存在的用户
- **THEN** 系统 MUST 返回 404

### Requirement: 配额覆写 UI 入口

配额覆写 UI MUST 同时在两处提供入口：

1. **D-07 Token 控制台配额面板**（`web/src/pages/dev/TokenDashboard/dashboard-ui.tsx` 的 QuotaPanel 子组件）
2. **A-04 用户管理表格**（`web/src/pages/UserManagement.tsx`，将在 change 4 实现）

两处调用同一 API（`PATCH /api/admin/users/{user_id}/quota`），UI 表现可不同但数据契约一致。

#### Scenario: Token 控制台调整配额

- **WHEN** 开发人员在 Token 控制台配额面板点击「调整配额」
- **THEN** 系统 MUST 弹出 Sheet 编辑 `token_monthly_limit` / `token_weekly_limit`
- **AND** 留空表示走默认（提交时传 null）
- **AND** 提交后调用 PATCH 接口

#### Scenario: 用户管理调整配额

- **WHEN** 管理员在用户管理表格点击某用户的「配额调整」
- **THEN** 系统 MUST 弹出与 Token 控制台相同的 Sheet
- **AND** 调用同一 PATCH 接口

### Requirement: 配额覆写二次确认

所有配额写操作 MUST 弹出二次确认对话框，提示用户「配额修改将立即生效，影响该用户的下一次 LLM 调用」。

#### Scenario: 二次确认弹窗

- **WHEN** 开发人员在 Sheet 中点击「保存」
- **THEN** 系统 MUST 弹出确认对话框
- **AND** 仅当用户点击「确认」后才发起 PATCH 请求

#### Scenario: 取消保存

- **WHEN** 用户在确认对话框点击「取消」
- **THEN** 系统 MUST 不发起请求
- **AND** Sheet 保持打开，保留用户输入
