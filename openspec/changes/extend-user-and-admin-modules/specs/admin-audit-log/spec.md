## ADDED Requirements

### Requirement: audit_logs 持久化

系统 SHALL 通过 `audit_logs` 表持久化管理员写操作历史。

表结构 MUST 满足：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | BIGINT | PK, AUTO_INCREMENT |
| `admin_id` | VARCHAR(36) | NOT NULL，关联 users.user_id |
| `action` | VARCHAR(64) | NOT NULL，7 类枚举之一 |
| `target_type` | VARCHAR(32) | NULL，被操作对象类型 |
| `target_id` | VARCHAR(64) | NULL，被操作对象 ID |
| `detail` | JSON | NULL，请求体或上下文 |
| `ip` | VARCHAR(45) | NULL，支持 IPv4/IPv6 |
| `created_at` | DATETIME | NOT NULL DEFAULT CURRENT_TIMESTAMP |

系统 MUST 在 `(admin_id, created_at)` 与 `(action, created_at)` 上建立索引。

#### Scenario: 写操作落审计

- **WHEN** 管理员成功执行任一写操作（7 类 action 之一）
- **THEN** `audit_logs` MUST 新增一行
- **AND** `admin_id` / `action` / `target_type` / `target_id` / `detail` / `ip` / `created_at` 字段正确填充

#### Scenario: 失败操作不入审计

- **WHEN** 管理员的写操作返回非 2xx（如 4xx 校验失败、5xx 异常）
- **THEN** `audit_logs` MUST NOT 新增行
- **AND** 避免审计噪声

### Requirement: 7 类 action 枚举

`audit_logs.action` MUST 为以下 7 个值之一：

| action | 触发路由 | target_type |
| --- | --- | --- |
| `review_decision` | `POST /api/reviews/{ticket_id}/decision` | `ticket` |
| `user_ban` / `user_unban` | `PATCH /api/admin/users/{user_id}` | `user` |
| `quota_update` | `PATCH /api/admin/users/{user_id}/quota` | `user` |
| `knowledge_delete` | `DELETE /api/admin/knowledge/{doc_id}` | `knowledge_doc` |
| `knowledge_rollback` | `POST /api/admin/knowledge/{doc_id}/rollback` | `knowledge_doc` |
| `prompt_activate` | `POST /api/admin/prompts/{agent_name}/versions/{version}/activate` | `prompt_version` |

#### Scenario: 推断 review_decision

- **WHEN** 管理员调用 `POST /api/reviews/TK-001/decision` 成功
- **THEN** audit_logs 新增行 `action="review_decision"`、`target_type="ticket"`、`target_id="TK-001"`

#### Scenario: 推断 quota_update

- **WHEN** 管理员调用 `PATCH /api/admin/users/U-123/quota` 成功
- **THEN** audit_logs 新增行 `action="quota_update"`、`target_type="user"`、`target_id="U-123"`

#### Scenario: 未覆盖路由不落审计

- **WHEN** 管理员调用未在映射表中的 admin 路由（如 `GET /api/admin/users`）
- **THEN** audit_logs MUST NOT 新增行
- **AND** 仅写操作（POST/PATCH/DELETE）且响应 2xx 才记录

### Requirement: 审计中间件自动写入

系统 SHALL 通过 FastAPI 中间件（`core/audit_middleware.py`）拦截 `POST/PATCH/DELETE /api/admin/*` 与 `POST /api/reviews/*/decision` 请求，**响应 2xx 后**异步写入 audit_logs。

- 中间件 MUST 从 session 推断 `admin_id`
- 中间件 MUST 从路由路径参数推断 `target_type` 与 `target_id`
- 中间件 MUST 从请求体推断 `detail`（过滤密码字段）
- 中间件 MUST 从请求头获取 `X-Forwarded-For` 或 `request.client.host` 作为 `ip`

#### Scenario: 中间件自动捕获

- **WHEN** 管理员带合法 session 调用任一映射表中的写操作
- **THEN** 中间件 MUST 在响应成功后自动写入 audit_logs
- **AND** 业务路由代码不需手动调用日志写入

#### Scenario: 密码字段过滤

- **WHEN** 请求体含 `password` / `old_password` / `new_password` / `api_key` 等敏感字段
- **THEN** 写入 audit_logs.detail 前 MUST 过滤这些字段
- **AND** detail 中不出现明文密码

### Requirement: 审计日志查询接口

系统 SHALL 提供 `GET /api/admin/audit-logs` 接口查询审计日志。

查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `admin_id` | string | 否 | 按管理员筛选 |
| `action` | string | 否 | 按 action 筛选 |
| `target_type` | string | 否 | 按目标类型筛选 |
| `start_date` | datetime | 否 | 起始时间（含） |
| `end_date` | datetime | 否 | 结束时间（含） |
| `page` | int | 否 | 默认 1 |
| `page_size` | int | 否 | 默认 50，最大 200 |

响应 MUST 包含 `{items: [...], total, page, page_size}`，items 按 `created_at` 倒序。

#### Scenario: 按时间筛选

- **WHEN** 管理员调用 `?start_date=2026-07-01T00:00:00&end_date=2026-07-01T23:59:59`
- **THEN** 系统 MUST 仅返回该日内的审计记录

#### Scenario: 按 action 筛选

- **WHEN** 管理员调用 `?action=quota_update`
- **THEN** 系统 MUST 仅返回 `action=quota_update` 的记录

#### Scenario: 分页

- **WHEN** 管理员调用 `?page=2&page_size=50`
- **THEN** 系统 MUST 返回第 2 页（跳过前 50 条，返回 50 条）
- **AND** `total` 字段反映筛选条件下的总数（非当前页数）

### Requirement: 前端 AuditLog 页面

前端 `web/src/pages/AuditLog.tsx` SHALL 提供筛选栏 + 表格 + JSON 详情展开。

- 顶部筛选栏：admin_id 输入 / action 下拉（7 选 1 或全部）/ target_type 下拉 / 时间区间选择
- 表格列：created_at / admin_id（含 nickname 关联查询）/ action / target_type / target_id / detail（可展开）/ ip
- 点击单行展开 `detail` JSON 树视图
- 分页控件

#### Scenario: 筛选查询

- **WHEN** 管理员选择 action=`review_decision` 并点击「查询」
- **THEN** 表格 MUST 仅显示 review_decision 记录
- **AND** 分页重置到第 1 页

#### Scenario: 展开详情

- **WHEN** 管理员点击某行的展开按钮
- **THEN** 系统 MUST 展开显示 `detail` JSON 树视图
- **AND** 支持折叠/展开子节点

#### Scenario: admin_id 显示 nickname

- **WHEN** 表格渲染 admin_id 列
- **THEN** 系统 MUST 关联 users 表显示 nickname（如 `Alice (U-001)`）
- **AND** 找不到用户时退化为仅显示 user_id
