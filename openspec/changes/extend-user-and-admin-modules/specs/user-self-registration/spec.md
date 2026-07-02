## ADDED Requirements

### Requirement: 用户自助注册接口

系统 SHALL 提供 `POST /api/auth/register` 接口，允许用户自助注册账户。

请求体 MUST 包含：

| 字段 | 类型 | 必填 | 校验规则 |
| --- | --- | --- | --- |
| `username` | string | 是 | 3-32 字符，字符集 `[a-zA-Z0-9_]` |
| `password` | string | 是 | ≥ 8 字符 |
| `nickname` | string | 否 | ≤ 32 字符，缺省时取 username |

接口 MUST 返回 `{user, session_token}`，并设置 session cookie。

#### Scenario: 注册成功

- **WHEN** 客户端提交 `{username: "alice", password: "secret123", nickname: "Alice"}`
- **THEN** 系统 MUST 在 `users` 表插入新行，`vip_level=0`、`status="active"`
- **AND** 返回 201 + `{user: {...}, session_token: "..."}`
- **AND** 注册成功即视为自动登录（无需再次调用 /login）

#### Scenario: 用户名已存在

- **WHEN** 客户端提交的 `username` 已存在
- **THEN** 系统 MUST 返回 409 + `{error: "username_taken"}`
- **AND** 不创建任何 session

#### Scenario: 密码强度不足

- **WHEN** 客户端提交 `password="abc"`（< 8 字符）
- **THEN** 系统 MUST 返回 422 + `{error: "password_too_weak"}`

#### Scenario: 用户名格式不合法

- **WHEN** 客户端提交 `username="ab"`（< 3 字符）或含非法字符（如 `alice!`）
- **THEN** 系统 MUST 返回 422 + `{error: "invalid_username"}`

### Requirement: 注册初始状态

新注册用户的初始字段 MUST 为：

| 字段 | 初始值 |
| --- | --- |
| `vip_level` | 0 |
| `status` | `active` |
| `token_monthly_limit` | NULL（走默认配额） |
| `token_weekly_limit` | NULL |
| `created_at` | 当前时间 |

系统 MUST NOT 给新注册用户分配管理员权限。

#### Scenario: 新用户初始 vip_level=0

- **WHEN** 任意用户注册成功
- **THEN** `users.vip_level` MUST 为 0
- **AND** `users.is_admin` MUST 为 false（或不存在该字段时无管理员权限）

#### Scenario: 新用户受默认配额约束

- **WHEN** 新注册用户首次提交工单触发 LLM 调用
- **THEN** 配额检查 MUST 按 `config.token_quota.monthly_limit` 默认值执行（因 `users.token_monthly_limit IS NULL`）

### Requirement: 前端注册页面

前端 `web/src/pages/Register.tsx` SHALL 提供单卡片居中（480px 宽）的注册表单。

- 表单字段：username / password / confirm_password / nickname（可选）
- 前端校验：username 格式、password ≥ 8、confirm_password 与 password 一致
- 提交调用 `POST /api/auth/register`
- 错误提示行内展示（用户名重复 / 密码强度不足等）
- 成功后自动登录并跳转 `/tickets`
- 底部链接「已有账户？去登录」跳转 `/login`

#### Scenario: 注册流程

- **WHEN** 用户在 `/register` 填写合法字段并提交
- **THEN** 系统 MUST 调用注册 API
- **AND** 成功后前端存储 session_token 并跳转 `/tickets`
- **AND** 失败时表单保留用户输入（除 password 外）

#### Scenario: 跳转登录

- **WHEN** 用户点击「已有账户？去登录」
- **THEN** 系统 MUST 跳转 `/login`

### Requirement: Login 页面增加注册入口

`web/src/pages/Login.tsx` MUST 在登录表单下方提供「去注册」链接，跳转 `/register`。

#### Scenario: 从登录跳转注册

- **WHEN** 用户在 Login 页点击「去注册」
- **THEN** 系统 MUST 跳转 `/register`
