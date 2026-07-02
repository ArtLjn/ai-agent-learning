## Context

本变更补齐 v2.0 重构后用户模块与管理员模块的功能缺口，使总功能数达到 30。设计依据：

- [07_前端页面与交互设计.md](../../docs/design-spec/01_正式设计/07_前端页面与交互设计.md) 第 2.1 / 2.2 节（页面布局）
- [01_核心功能需求.md](../../docs/design-spec/02_产品需求/01_核心功能需求.md)（验收标准）
- [11_RAG服务独立项目设计.md](../../docs/design-spec/01_正式设计/11_RAG服务独立项目设计.md)（A-03 透传目标）
- [05_数据存储设计.md](../../docs/design-spec/01_正式设计/05_数据存储设计.md)（users 表基础）

本期不引入新依赖，所有改动落在现有 FastAPI + React + SQLAlchemy 技术栈内。

## Goals / Non-Goals

### Goals

- 落地用户模块 3 个新功能（U-02 / U-03 / U-04），与现有 Login / Tickets 流程无缝衔接。
- 落地管理员模块 3 个新功能（A-03 / A-06 / A-07），A-07 操作日志覆盖 7 类管理员写操作。
- A-04 用户管理页面与 change 3 的配额接口对齐，UI 入口一致。
- 所有接口走 `require_admin`（admin）或 session（user）鉴权，密钥/密码字段在 A-06 中严格脱敏。

### Non-Goals

- **不做** 用户头像上传 / 文件存储（毕设范围无对象存储）。
- **不做** 密码强度算法评估（仅长度校验 ≥ 8）。
- **不做** 注册邮件验证 / 短信验证（无邮件/短信网关）。
- **不做** OAuth 第三方登录（GitHub / Google）。
- **不做** A-06 配置在线修改（只读视图，配置变更走环境变量）。
- **不做** A-07 审计日志的实时推送（只查询历史，不做 WebSocket）。
- **不做** audit_logs 表的归档与清理（毕设范围内数据量小）。

## 关键决策

### 决策 1：注册成功自动登录，不发激活邮件

U-02 注册成功后**立即创建 session**并跳转 `/tickets`，不发激活邮件、不需管理员审核。

**理由**：

1. 毕设无邮件/短信网关。
2. 答辩演示场景下"注册 → 立即可用"是基本预期。
3. 防滥用通过 D-07 配额覆写 + 默认月配额兜底（注册即受 `config.token_quota.monthly_limit` 限制）。

**代价**：理论上可被刷账号，但毕设无生产风险。

### 决策 2：用户名唯一性前后端双重校验

前端 Register.tsx 仅做格式校验（用户名长度 3-32、字符集），**唯一性校验由后端 `POST /api/auth/register` 返回 409 保证**。

**理由**：

1. 前端无法保证时序一致性（两个用户同时注册同名）。
2. 后端通过 DB UNIQUE 约束 + 捕获 IntegrityError 返回 409 是唯一可靠路径。

### 决策 3：修改密码成功后主动失效 session

`POST /api/users/me/password` 成功后**服务端清除该用户的所有 session**，前端跳转登录页。

**理由**：

1. 密码已变更，旧 session 凭证理论上应失效（安全规范）。
2. 即便 session 是无状态 JWT，也应通过黑名单或版本号失效——毕设简化为「修改后强制重新登录」。

**代价**：用户体验略差，但符合安全预期。

### 决策 4：A-06 配置脱敏策略——直接省略字段，不返回 `"***"`

`GET /api/admin/config` 响应中，密钥类字段（如 `OPENAI_API_KEY` / `DB_PASSWORD`）**不返回字段**（直接省略 key），而非返回 `"***"`。

**理由**：

1. 返回 `"***"` 会让前端误以为是真实值，可能造成 UI 显示混乱。
2. 省略字段后前端代码可以安全地「字段存在则显示」，无字段则不渲染该行。
3. 符合 [07 号文档 2.2.2](../../docs/design-spec/01_正式设计/07_前端页面与交互设计.md) 明确约束。

**展示策略**：base URL 这类非密钥但敏感的配置，**部分脱敏**（如 `https://api.openai.com/v1` 完整显示，但 `https://internal-proxy.xxx.com` 脱敏为 `https://***.xxx.com`）。毕设范围内仅做"密钥全脱敏、URL 完整显示"。

### 决策 5：A-07 audit_logs 通过 FastAPI 中间件自动写入，不依赖各路由手动调用

写操作日志通过 `core/audit_middleware.py` 中间件拦截所有 `POST/PATCH/DELETE /api/admin/*` 请求，**响应成功（2xx）后**异步写入 `audit_logs`。

**记录字段**：admin_id（从 session）、action（从路由路径推断）、target_type、target_id（从路径参数）、detail（请求体 JSON，过滤密码字段）、ip、created_at。

**理由**：

1. 中间件统一拦截，避免每个写路由手动写日志代码（易遗漏）。
2. 仅记录成功响应，避免噪声（失败请求不写入）。
3. action 通过路径推断（如 `POST /api/admin/users/{id}/quota` → action=`quota_update`），用查表维护映射。

**代价**：action 推断有少量边界 case（如自定义路由），通过显式映射表覆盖 7 类操作即可。

### 决策 6：A-03 知识库版本回滚主系统只透传，不存版本表

A-03 回滚请求由主系统 `POST /api/admin/knowledge/{doc_id}/rollback?version=X` 透传到 rag-service，**主系统不维护文档版本表**。

**理由**：

1. 11 号文档明确文档版本管理是 rag-service 的职责。
2. 主系统重复维护版本会造成双写不一致。
3. 回滚操作影响的是 Qdrant 中的向量数据，本就由 rag-service 操作。

**回滚接口的审计**：A-03 回滚操作通过 A-07 中间件自动落 `audit_logs`（action=`knowledge_rollback`），不依赖 rag-service。

### 决策 7：A-04 用户管理与 D-07 配额 UI 共用 Sheet 组件

`UserManagement.tsx` 表格中「配额调整」按钮点击后弹出的 Sheet，**与 TokenDashboard 配额面板的 Sheet 复用同一个 React 组件**（`web/src/components/QuotaEditSheet.tsx`）。

**理由**：

1. 数据契约一致（同一 PATCH 接口）。
2. 减少代码重复，避免两处 UI 漂移。
3. 单一组件统一二次确认逻辑。
