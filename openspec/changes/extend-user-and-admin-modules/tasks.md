## 1. U-02 用户自助注册

- [ ] 1.1 后端 `src/multi_agent_system/api/auth.py` 新增 `POST /api/auth/register`，请求体 `{username, password, nickname?}`，校验用户名 3-32 字符 + 密码 ≥ 8 字符
  - file: `src/multi_agent_system/api/auth.py`（修改）
  - 设计依据：[07_前端页面与交互设计.md:29-38](../../docs/design-spec/01_正式设计/07_前端页面与交互设计.md)
- [ ] 1.2 注册接口捕获 IntegrityError 返回 409 + `{error: "username_taken"}`
  - file: `src/multi_agent_system/api/auth.py`
- [ ] 1.3 注册成功后立即创建 session 并返回 `{user, session_token}`，初始 `vip_level=0`
  - file: `src/multi_agent_system/api/auth.py`
- [ ] 1.4 前端 `web/src/pages/Register.tsx`：单卡片居中（480px），字段含用户名 / 密码 / 确认密码 / 昵称（可选），提交调用 register
  - file: `web/src/pages/Register.tsx`（新增）
- [ ] 1.5 Register 成功后自动登录并跳转 `/tickets`；底部「已有账户？去登录」链接到 Login
  - file: `web/src/pages/Register.tsx`
- [ ] 1.6 在 `web/src/App.tsx` 注册 `/register` 路由；Login 页底部加「去注册」链接
  - file: `web/src/App.tsx`、`web/src/pages/Login.tsx`（修改）
- [ ] 1.7 在 `web/src/api/auth.ts` 新增 `register(payload)` 调用函数
  - file: `web/src/api/auth.ts`（修改）

## 2. U-03 用户信息管理

- [ ] 2.1 后端 `src/multi_agent_system/models/user.py` 检查并补齐字段：`nickname` / `contact` / `preferred_categories`（JSON 数组），缺失则 ALTER TABLE
  - file: `src/multi_agent_system/models/user.py`（修改）
- [ ] 2.2 后端新增 `src/multi_agent_system/api/users.py`，实现 `GET /api/users/me` 返回当前登录用户完整信息（不含 password_hash）
  - file: `src/multi_agent_system/api/users.py`（新增）
- [ ] 2.3 实现 `PATCH /api/users/me`，可改字段：`nickname` / `contact` / `preferred_categories`；不可改字段（`user_id` / `username` / `vip_level` / `created_at`）请求中出现则忽略或返回 422
  - file: `src/multi_agent_system/api/users.py`
- [ ] 2.4 在 `routes.py` 注册 `/api/users/me` 路由，走 session 鉴权（当前用户）
  - file: `src/multi_agent_system/api/routes.py`（修改）
- [ ] 2.5 前端 `web/src/pages/Profile.tsx` 卡片 1「基本信息」：可改字段表单 + 只读字段展示 + 保存按钮
  - file: `web/src/pages/Profile.tsx`（新增）
  - 设计依据：[07_前端页面与交互设计.md:42-50](../../docs/design-spec/01_正式设计/07_前端页面与交互设计.md)
- [ ] 2.6 `preferred_categories` 多选组件（technical / billing / complaint / inquiry 4 项）
  - file: `web/src/pages/Profile.tsx`
- [ ] 2.7 在 `web/src/api/users.ts`（新增或修改）添加 `getMe` / `updateMe` 调用函数
  - file: `web/src/api/users.ts`
- [ ] 2.8 侧边栏用户菜单加「个人资料」入口，注册 `/profile` 路由
  - file: `web/src/components/Sidebar.tsx`、`web/src/App.tsx`（修改）

## 3. U-04 修改密码

- [ ] 3.1 后端 `src/multi_agent_system/api/users.py` 实现 `POST /api/users/me/password`，请求体 `{old_password, new_password}`，校验旧密码（bcrypt verify）
  - file: `src/multi_agent_system/api/users.py`
- [ ] 3.2 校验规则：new_password ≥ 8、与 old_password 不同、两次输入一致（前端 + 后端双重）
  - file: `src/multi_agent_system/api/users.py`
- [ ] 3.3 旧密码错误返回 401 + `{error: "invalid_credentials"}`（统一提示「凭证错误」防泄漏）
  - file: `src/multi_agent_system/api/users.py`
- [ ] 3.4 成功后清除该用户所有 session，返回 `{success: true, redirect: "/login"}`
  - file: `src/multi_agent_system/api/users.py`
- [ ] 3.5 前端 Profile.tsx 卡片 2「修改密码」：旧密码 / 新密码 / 确认新密码 + 校验提示
  - file: `web/src/pages/Profile.tsx`
  - 设计依据：[07_前端页面与交互设计.md:52-56](../../docs/design-spec/01_前端页面与交互设计.md)
- [ ] 3.6 成功后前端清除本地 session token，跳转 `/login`
  - file: `web/src/pages/Profile.tsx`
- [ ] 3.7 在 `web/src/api/users.ts` 添加 `changePassword(payload)` 调用函数
  - file: `web/src/api/users.ts`

## 4. A-03 知识库版本回滚

- [ ] 4.1 后端新增 `src/multi_agent_system/api/admin_knowledge.py`（或在现有 knowledge 路由追加），实现 `POST /api/admin/knowledge/{doc_id}/rollback?version=X` 透传到 rag-service
  - file: `src/multi_agent_system/api/admin_knowledge.py`（新增或修改）
  - 设计依据：[11_RAG服务独立项目设计.md](../../docs/design-spec/01_正式设计/11_RAG服务独立项目设计.md)
- [ ] 4.2 rag-service 端的回滚端点契约：`POST /documents/{doc_id}/rollback?version=X`，主系统透传时透传 admin 身份
  - file: `src/multi_agent_system/api/admin_knowledge.py`
- [ ] 4.3 rag-service 不可用时主系统返回 502 + `{error: "rag_service_unavailable"}`，不抛异常
  - file: `src/multi_agent_system/api/admin_knowledge.py`
- [ ] 4.4 前端 Knowledge.tsx（管理员视图）文档列表行加「版本历史」按钮，点击弹出 Sheet 展示版本列表 + 「回滚到此版本」按钮
  - file: `web/src/pages/Knowledge.tsx`（修改）
  - 设计依据：[07_前端页面与交互设计.md:59-67](../../docs/design-spec/01_前端页面与交互设计.md)
- [ ] 4.5 回滚操作二次确认（影响生产知识库）
  - file: `web/src/pages/Knowledge.tsx`
- [ ] 4.6 在 `web/src/api/admin.ts` 添加 `getDocVersions(doc_id)` / `rollbackDoc(doc_id, version)` 调用函数
  - file: `web/src/api/admin.ts`（修改）

## 5. A-06 系统配置查看（只读脱敏）

- [ ] 5.1 后端新增 `src/multi_agent_system/api/admin_config.py`，实现 `GET /api/admin/config` 返回 6 类配置摘要
  - file: `src/multi_agent_system/api/admin_config.py`（新增）
  - 设计依据：[07_前端页面与交互设计.md:81-96](../../docs/design-spec/01_正式设计/07_前端页面与交互设计.md)
- [ ] 5.2 配置来源：LLM 配置读 `config.yaml` 的 llm 段、向量存储读 qdrant 段、RAG 服务读 rag_service 段、数据库读 database 段、Prompt 版本读 `prompt_versions` 表激活版本、默认配额读 token_quota 段
  - file: `src/multi_agent_system/api/admin_config.py`
- [ ] 5.3 脱敏规则：密钥字段（API_KEY / PASSWORD / SECRET）直接省略字段不返回；URL 完整显示
  - file: `src/multi_agent_system/api/admin_config.py`
  - 设计依据：design.md 决策 4
- [ ] 5.4 在 `routes.py` 注册路由，走 `require_admin` 鉴权
  - file: `src/multi_agent_system/api/routes.py`（修改）
- [ ] 5.5 前端 `web/src/pages/SystemConfig.tsx`：6 张只读卡片网格 + 顶部红色提示条「只读视图，配置修改请联系开发人员通过环境变量调整」
  - file: `web/src/pages/SystemConfig.tsx`（新增）
  - 设计依据：[07_前端页面与交互设计.md:81-96](../../docs/design-spec/01_正式设计/07_前端页面与交互设计.md)
- [ ] 5.6 修改 `web/src/pages/Settings.tsx`：重定向到 `/config`（SystemConfig.tsx）
  - file: `web/src/pages/Settings.tsx`（修改）
- [ ] 5.7 侧边栏「设置」入口改为「系统配置」
  - file: `web/src/components/Sidebar.tsx`（修改）

## 6. A-07 操作日志审计

- [ ] 6.1 新增 ORM `src/multi_agent_system/models/audit_log.py`，字段：`id` / `admin_id` / `action` / `target_type` / `target_id` / `detail`(JSON) / `ip` / `created_at`，索引 `(admin_id, created_at)` / `(action, created_at)`
  - file: `src/multi_agent_system/models/audit_log.py`（新增）
- [ ] 6.2 在 `core/database.py` 注册 AuditLog ORM 与表创建
  - file: `src/multi_agent_system/core/database.py`（修改）
- [ ] 6.3 新增 `src/multi_agent_system/core/audit_middleware.py` FastAPI 中间件，拦截 `POST/PATCH/DELETE /api/admin/*`，响应 2xx 后异步写入 audit_logs
  - file: `src/multi_agent_system/core/audit_middleware.py`（新增）
  - 设计依据：design.md 决策 5
- [ ] 6.4 action 推断映射表（7 类）：`POST /api/reviews/{id}/decision` → `review_decision`、`PATCH /api/admin/users/{id}` → `user_ban`/`user_unban`、`PATCH /api/admin/users/{id}/quota` → `quota_update`、`DELETE /api/admin/knowledge/{doc_id}` → `knowledge_delete`、`POST /api/admin/knowledge/{doc_id}/rollback` → `knowledge_rollback`、`POST /api/admin/prompts/{agent}/versions/{v}/activate` → `prompt_activate`
  - file: `src/multi_agent_system/core/audit_middleware.py`
- [ ] 6.5 detail 字段过滤密码类字段（如 `password` / `old_password` / `new_password`），不写入明文
  - file: `src/multi_agent_system/core/audit_middleware.py`
- [ ] 6.6 在 FastAPI app 启动时注册中间件
  - file: `src/multi_agent_system/main.py` 或同等入口（修改）
- [ ] 6.7 后端新增 `src/multi_agent_system/api/admin_audit.py`，实现 `GET /api/admin/audit-logs`，查询参数：`admin_id` / `action` / `target_type` / `start_date` / `end_date` / `page` / `page_size`
  - file: `src/multi_agent_system/api/admin_audit.py`（新增）
- [ ] 6.8 在 `routes.py` 注册 audit-logs 路由，走 `require_admin`
  - file: `src/multi_agent_system/api/routes.py`（修改）
- [ ] 6.9 前端 `web/src/pages/AuditLog.tsx`：顶部筛选栏（admin_id / action / target_type / 时间区间）+ 表格（created_at / admin + nickname / action / target_type / target_id / detail 可展开 / ip）
  - file: `web/src/pages/AuditLog.tsx`（新增）
  - 设计依据：[07_前端页面与交互设计.md:98-109](../../docs/design-spec/01_正式设计/07_前端页面与交互设计.md)
- [ ] 6.10 点击单行展开 `detail` JSON 树视图
  - file: `web/src/pages/AuditLog.tsx`
- [ ] 6.11 在 `web/src/api/admin.ts` 添加 `getAuditLogs(filters)` 调用函数
  - file: `web/src/api/admin.ts`（修改）

## 7. A-04 用户管理（复用 change 3 配额接口）

- [ ] 7.1 后端：复用 change 3 已实现的 `PATCH /api/admin/users/{user_id}/quota` + 新增 `GET /api/admin/users`（分页 + 筛选）+ `PATCH /api/admin/users/{user_id}`（封禁/解封，status 切换）
  - file: `src/multi_agent_system/api/admin_users.py`（新增）
  - 设计依据：[07_前端页面与交互设计.md:69-79](../../docs/design-spec/01_正式设计/07_前端页面与交互设计.md)
- [ ] 7.2 `GET /api/admin/users` 支持筛选：status（active/banned）、关键字（username/nickname）、vip_level；分页参数 page / page_size
  - file: `src/multi_agent_system/api/admin_users.py`
- [ ] 7.3 `PATCH /api/admin/users/{user_id}` 仅允许修改 status 字段（active ↔ banned），其他字段忽略
  - file: `src/multi_agent_system/api/admin_users.py`
- [ ] 7.4 在 `routes.py` 注册路由，走 `require_admin`
  - file: `src/multi_agent_system/api/routes.py`（修改）
- [ ] 7.5 前端 `web/src/pages/UserManagement.tsx`：顶部筛选栏 + 表格（username / nickname / status / vip_level / token_monthly_limit（NULL 显示「默认」）/ token_weekly_limit / created_at）+ 操作列
  - file: `web/src/pages/UserManagement.tsx`（新增）
- [ ] 7.6 操作列按钮：封禁/解封（切换 status）+ 配额调整（弹 QuotaEditSheet）
  - file: `web/src/pages/UserManagement.tsx`
- [ ] 7.7 提取共享组件 `web/src/components/QuotaEditSheet.tsx`，与 TokenDashboard 配额面板共用（决策 7）
  - file: `web/src/components/QuotaEditSheet.tsx`（新增）
- [ ] 7.8 所有写操作二次确认
  - file: `web/src/pages/UserManagement.tsx`
- [ ] 7.9 在侧边栏管理员菜单加「用户管理」入口，注册 `/admin/users` 路由
  - file: `web/src/components/Sidebar.tsx`、`web/src/App.tsx`（修改）

## 8. 单元测试与集成测试

- [ ] 8.1 `tests/api/test_auth_register.py`：注册成功 / 用户名重复 409 / 密码强度不足 422 / 自动登录 session 创建
- [ ] 8.2 `tests/api/test_users_me.py`：信息读取 / 修改 nickname / preferred_categories 数组持久化 / 不可改字段被忽略
- [ ] 8.3 `tests/api/test_users_password.py`：旧密码错误 401 / 新密码相同 422 / 成功后 session 失效
- [ ] 8.4 `tests/api/test_admin_config.py`：6 类配置完整返回 / API_KEY 字段省略 / URL 完整显示
- [ ] 8.5 `tests/api/test_admin_audit.py`：中间件自动写入 / 7 类 action 推断正确 / 密码字段过滤 / 筛选查询
- [ ] 8.6 `tests/api/test_admin_knowledge_rollback.py`：透传 rag-service / 502 降级 / 回滚操作落 audit_logs
- [ ] 8.7 `tests/api/test_admin_users.py`：分页查询 / 筛选 / 封禁/解封 / 配额 PATCH（复用 change 3 测试）
