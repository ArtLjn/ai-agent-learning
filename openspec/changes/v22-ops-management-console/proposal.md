## Why

老师反馈中“开发人员是干嘛的”已经明确回答为系统运维和智能链路调优，而不是直接处理业务工单。旧 `add-developer-workbench` 提案仍使用开发人员模块口径，且包含已取消的用户配额等内容，需要按 v2.2 定稿拆成系统运维管理端实施包。

## What Changes

- 新增系统运维管理端业务闭环：账号管理、流程监控、策略调试、系统健康。
- 将账号管理聚合到系统运维管理端，覆盖企业内部员工、服务台处理人员和系统运维人员的角色状态维护。
- 将状态机画布、Trace 决策树、节点输入输出和异常日志定位作为流程监控能力。
- 将 Prompt 版本、RAG 检索调试、智能体调用统计作为策略调试能力。
- 将 Token 用量、服务健康检查和依赖状态展示作为系统健康能力。
- 明确不做用户计费、per-user 配额、企业级 SSO、多租户权限。

## Capabilities

### New Capabilities

- `ops-management-console`: 系统管理员和开发运维人员治理账号、流程、策略和系统健康的能力。

### Modified Capabilities

无。

## Impact

- 前端：`web/src/pages/dev/SpanTreeView.tsx`、Prompt/RAG/Token/Health 页面、账号管理页。
- 后端：`/api/admin/traces/*`、`/api/admin/prompts/*`、`/api/admin/rag/debug`、`/api/admin/stats/*`、`/api/admin/health`、`/api/admin/users`。
- 数据：`spans.metadata.decision`、`prompt_versions`、`token_daily_stats`、账号角色状态。
- 测试：Trace 决策树、状态机画布、Prompt 版本切换、RAG 调试透传、Token 聚合、健康检查和账号封禁。
