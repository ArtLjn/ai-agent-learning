## ADDED Requirements

### Requirement: 获取当前用户信息

系统 SHALL 提供 `GET /api/users/me` 接口返回当前登录用户的完整信息（不含 `password_hash`）。

- 接口 MUST 通过 session 鉴权（当前用户）
- 响应 MUST 包含：`user_id` / `username` / `nickname` / `contact` / `vip_level` / `preferred_categories` / `created_at` / `status`
- 响应 MUST NOT 包含 `password_hash`

#### Scenario: 已登录用户获取信息

- **WHEN** 用户带有效 session 调用 `GET /api/users/me`
- **THEN** 系统 MUST 返回 200 + 用户完整信息（无 password_hash）

#### Scenario: 未登录访问

- **WHEN** 无 session 或 session 失效
- **THEN** 系统 MUST 返回 401

### Requirement: 修改当前用户信息

系统 SHALL 提供 `PATCH /api/users/me` 接口修改当前用户信息。

可改字段：

| 字段 | 类型 | 校验 |
| --- | --- | --- |
| `nickname` | string | ≤ 32 字符 |
| `contact` | string | ≤ 128 字符（电话/邮箱格式由前端校验，后端仅长度） |
| `preferred_categories` | array | 子集 of `[technical, billing, complaint, inquiry]` |

不可改字段（请求中出现则忽略，不报错）：`user_id` / `username` / `vip_level` / `created_at` / `status` / `is_admin` / `token_*_limit`。

#### Scenario: 修改昵称与偏好分类

- **WHEN** 用户提交 `{nickname: "Alice L", preferred_categories: ["technical", "billing"]}`
- **THEN** 系统 MUST 更新对应字段
- **AND** 返回 200 + 更新后的完整用户信息

#### Scenario: 试图修改 user_id 被忽略

- **WHEN** 用户提交 `{user_id: "U-999", nickname: "hacker"}`
- **THEN** 系统 MUST 忽略 `user_id` 字段
- **AND** 仅更新 `nickname`
- **AND** 返回 200（不报错）

#### Scenario: preferred_categories 含非法值

- **WHEN** 用户提交 `{preferred_categories: ["technical", "unknown_cat"]}`
- **THEN** 系统 MUST 返回 422 + `{error: "invalid_category"}`

### Requirement: 修改密码接口

系统 SHALL 提供 `POST /api/users/me/password` 接口允许用户修改密码。

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `old_password` | string | 是 | 旧密码 |
| `new_password` | string | 是 | 新密码，≥ 8 字符 |

校验规则：

1. `old_password` 必须与 DB 中 `password_hash` 匹配（bcrypt verify）
2. `new_password` 长度 ≥ 8
3. `new_password` 不等于 `old_password`
4. 前端 + 后端双重校验

#### Scenario: 修改成功

- **WHEN** 用户提交正确的 `old_password` 与合法的 `new_password`
- **THEN** 系统 MUST 更新 `password_hash`（bcrypt 重新哈希）
- **AND** 清除该用户所有现有 session
- **AND** 返回 200 + `{success: true, redirect: "/login"}`

#### Scenario: 旧密码错误

- **WHEN** 用户提交的 `old_password` 与 DB 不匹配
- **THEN** 系统 MUST 返回 401 + `{error: "invalid_credentials"}`
- **AND** 统一提示「凭证错误」（不泄漏具体是哪个字段错）

#### Scenario: 新密码与旧密码相同

- **WHEN** `new_password == old_password`
- **THEN** 系统 MUST 返回 422 + `{error: "password_same_as_old"}`

#### Scenario: 新密码强度不足

- **WHEN** `new_password` 长度 < 8
- **THEN** 系统 MUST 返回 422 + `{error: "password_too_weak"}`

### Requirement: 修改密码后 session 立即失效

密码修改成功后，系统 MUST 使该用户所有现有 session 失效，强制重新登录。

#### Scenario: 旧 session 不可用

- **WHEN** 用户修改密码成功后，使用旧 session_token 调用任意需鉴权接口
- **THEN** 系统 MUST 返回 401
- **AND** 用户需通过 `/login` 重新登录

### Requirement: 前端 Profile 页面双卡片布局

前端 `web/src/pages/Profile.tsx` SHALL 提供两个纵向排列的卡片。

**卡片 1：基本信息（U-03）**

- 可改字段表单：nickname / contact / preferred_categories（多选）
- 只读字段展示：user_id / username / vip_level / created_at
- 保存按钮：调用 `PATCH /api/users/me`
- 成功提示行内展示

**卡片 2：修改密码（U-04）**

- 字段：old_password / new_password / confirm_new_password
- 校验：new_password ≥ 8、与 old_password 不同、两次输入一致
- 提交：调用 `POST /api/users/me/password`
- 成功后跳转 `/login`

#### Scenario: 修改基本信息

- **WHEN** 用户在卡片 1 修改 nickname 并保存
- **THEN** 系统 MUST 调用 PATCH 接口
- **AND** 成功后表单显示「已保存」提示

#### Scenario: 修改密码后跳转

- **WHEN** 用户在卡片 2 成功修改密码
- **THEN** 前端 MUST 清除本地 session_token
- **AND** 跳转 `/login` 显示「密码已修改，请重新登录」提示

### Requirement: 不可改字段只读展示

Profile.tsx 卡片 1 MUST 以只读样式展示 `user_id` / `username` / `vip_level` / `created_at`，禁止编辑。

#### Scenario: 只读字段不可编辑

- **WHEN** 用户在 Profile.tsx 查看基本信息卡片
- **THEN** `user_id` / `username` / `vip_level` / `created_at` MUST 显示为只读（灰色文本或 disabled input）
- **AND** 不出现编辑入口
