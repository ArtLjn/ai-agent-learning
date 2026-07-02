## Why

v2.0 重构把总功能数明确为 30 个（详见 [system-module-architecture-v2-ascii.md](../../docs/design-spec/assets/system-module-architecture-v2-ascii.md)），其中用户模块 8 个、管理员模块 7 个。当前实现仅覆盖：

| 模块 | 已实现 | v2.0 缺口 |
| --- | --- | --- |
| 用户模块 | U-01 登录、U-05 工单提交、U-06 工单查询、U-07 消息补充、U-08 满意度反馈 | **U-02 注册、U-03 用户信息管理、U-04 修改密码** |
| 管理员模块 | A-01 审核工作台、A-02 知识库管理、A-04 用户管理（部分）、A-05 决策采纳率 | **A-03 知识库版本回滚、A-06 系统配置查看、A-07 操作日志审计** |

答辩反馈"工作量不够 + 模块要按角色分层"要求 30 功能全部落地。这 7 个缺口不补，论文 5 章「系统实现」与功能编号清单无法对齐，[01_核心功能需求.md](../../docs/design-spec/02_产品需求/01_核心功能需求.md) 的验收标准也通不过。

本变更补齐这 7 个功能（用户 3 + 管理员 3 + A-04 完善 1），使总数达到 30。

## What Changes

### 用户模块新增

- **U-02 用户自助注册**：新增 `POST /api/auth/register` 接口 + `web/src/pages/Register.tsx`，注册成功自动登录跳转工单列表。用户名唯一性校验、密码强度校验（≥ 8 字符）、初始 `vip_level=0`。
- **U-03 用户信息管理**：新增 `GET /api/users/me` + `PATCH /api/users/me` 接口 + `web/src/pages/Profile.tsx` 卡片 1。可改字段：昵称、联系方式、偏好分类；只读字段：`user_id` / `username` / `vip_level` / `created_at`。
- **U-04 修改密码**：新增 `POST /api/users/me/password` 接口 + `Profile.tsx` 卡片 2。校验旧密码、新密码 ≥ 8、与旧密码不同、两次输入一致；成功后 session 失效。

### 管理员模块新增

- **A-03 知识库版本回滚**：依赖 rag-service 的文档版本管理 API（详见 [11_RAG服务独立项目设计.md](../../docs/design-spec/01_正式设计/11_RAG服务独立项目设计.md)），主系统新增 `POST /api/admin/knowledge/{doc_id}/rollback?version=X` 透传。
- **A-06 系统配置查看**：新增 `GET /api/admin/config`（只读脱敏）+ `web/src/pages/SystemConfig.tsx`。展示 LLM / Qdrant / rag-service / 数据库 / Prompt 版本 / 默认配额 6 类配置；密钥/密码字段不返回（不展示 `"***"`）。
- **A-07 操作日志审计**：新增 `audit_logs` 表 + 写入中间件 + `GET /api/admin/audit-logs` 接口 + `web/src/pages/AuditLog.tsx`。覆盖 7 类管理员写操作：审核决策 / 封禁 / 解封 / 配额调整 / 知识库删除 / 版本回滚 / Prompt 激活。
- **A-04 用户管理完善**：新增 `web/src/pages/UserManagement.tsx`，与 D-07 配额管理共享 `PATCH /api/admin/users/{user_id}/quota` 接口（接口 spec 见 change 3 的 `dev-quota-management`）。

## Capabilities

### New Capabilities

- `user-self-registration`（U-02）
- `user-profile-management`（U-03 + U-04）
- `knowledge-version-rollback`（A-03）
- `system-config-readonly-view`（A-06）
- `admin-audit-log`（A-07）

### Modified Capabilities

无。`PATCH /api/admin/users/{user_id}/quota` 接口与配额 UI 已在 change 3 的 `dev-quota-management` spec 中定义，本变更的 A-04 仅复用该接口，不修改其契约。

## Impact

### 后端代码

- `src/multi_agent_system/api/auth.py`（修改）：新增 `/register` 端点
- `src/multi_agent_system/api/users.py`（新增）：`/api/users/me` GET/PATCH、`/api/users/me/password` POST
- `src/multi_agent_system/api/admin_config.py`（新增）：`/api/admin/config`
- `src/multi_agent_system/api/admin_audit.py`（新增）：`/api/admin/audit-logs`
- `src/multi_agent_system/api/admin_knowledge.py`（新增或修改）：`/rollback` 端点透传 rag-service
- `src/multi_agent_system/models/audit_log.py`（新增 ORM）
- `src/multi_agent_system/models/user.py`（修改）：补字段（nickname / contact / preferred_categories）若尚未存在
- `src/multi_agent_system/core/audit_middleware.py`（新增）：FastAPI 中间件，拦截 `/api/admin/*` 写操作自动落 `audit_logs`
- `src/multi_agent_system/api/routes.py`（修改）：注册上述路由

### 前端代码

- `web/src/pages/Register.tsx`（新增）
- `web/src/pages/Profile.tsx`（新增）
- `web/src/pages/SystemConfig.tsx`（新增）
- `web/src/pages/AuditLog.tsx`（新增）
- `web/src/pages/UserManagement.tsx`（新增）
- `web/src/pages/Settings.tsx`（修改）：重定向到 SystemConfig.tsx（07 号文档 2.2 节）
- `web/src/api/auth.ts` / `users.ts` / `admin.ts`（修改）：新增对应调用函数
- `web/src/App.tsx`（修改）：注册新路由
- `web/src/components/Sidebar.tsx`（修改）：补菜单项

### 数据库

- 新增表 `audit_logs`（schema 见 design.md）
- `users` 表若缺字段需 `ALTER TABLE` 补 `nickname` / `contact` / `preferred_categories`（视当前 schema 而定）

### 测试

- `tests/api/test_auth_register.py`：注册成功 / 用户名重复 / 密码强度不足
- `tests/api/test_users_me.py`：信息读取 / 修改 / 字段校验
- `tests/api/test_users_password.py`：旧密码错误 / 新密码相同 / 成功后 session 失效
- `tests/api/test_admin_config.py`：脱敏字段不返回
- `tests/api/test_admin_audit.py`：中间件自动写入 / 筛选查询
- `tests/api/test_admin_knowledge_rollback.py`：透传 rag-service

### 工时预估

约 1-2 天。
