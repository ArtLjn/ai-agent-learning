## 1. 前端 DevConsole 主页与路由

- [ ] 1.1 新建 `web/src/pages/DevConsole.tsx`，左侧导航 7 项（Trace / Prompt / RAG / Token / AgentStats / Health / Quota），右侧 Outlet 渲染子页
  - file: `web/src/pages/DevConsole.tsx`（新增）
  - 设计依据：[13_开发人员工作台设计.md:374-405](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md)
- [ ] 1.2 在 `web/src/App.tsx` 注册 `/dev`、`/dev/traces`、`/dev/prompts`、`/dev/rag`、`/dev/tokens`、`/dev/stats`、`/dev/health`、`/dev/quota` 路由
  - file: `web/src/App.tsx`（修改）
- [ ] 1.3 在侧边栏导航（`web/src/components/Sidebar.tsx` 或同等位置）新增"开发工作台"入口，仅对 admin 角色可见
  - file: `web/src/components/Sidebar.tsx`（修改）
- [ ] 1.4 新增 `web/src/api/admin.ts` 中 traces / prompts / rag / stats / health / quota 调用函数签名（与各子页实现同步补全）
  - file: `web/src/api/admin.ts`（修改）

## 2. D-01 Trace 决策树（依赖 change 2 决策点埋点修复）

- [ ] 2.1 新建 `web/src/pages/dev/SpanTreeView.tsx`，复用 `web/src/components/trace/TraceGantt.tsx` 渲染 span 树，对含 `metadata.decision` 的节点加高亮徽章
  - file: `web/src/pages/dev/SpanTreeView.tsx`（新增）
  - 复用：`web/src/components/trace/TraceGantt.tsx`、`SpanDetailSheet.tsx`、`DecisionTimeline.tsx`、`DecisionCard.tsx`
- [ ] 2.2 SpanTreeView 顶部加工单 ID 输入框，支持 URL query `?ticket_id=...&span_id=...`（与 Token 控制台联动）
  - file: `web/src/pages/dev/SpanTreeView.tsx`
  - 设计依据：[13_开发人员工作台设计.md:266-273](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md)
- [ ] 2.3 后端新增 `src/multi_agent_system/api/admin_traces.py`，实现 `GET /api/admin/traces/{ticket_id}` 返回完整 span 树
  - file: `src/multi_agent_system/api/admin_traces.py`（新增）
- [ ] 2.4 实现 `GET /api/admin/traces/{ticket_id}/spans/{span_id}` 返回 span 详情（含 metadata.decision / token_usage / rag_stats）
  - file: `src/multi_agent_system/api/admin_traces.py`
- [ ] 2.5 实现 `GET /api/admin/traces/{ticket_id}/decisions`，从 spans.metadata.decision 抽取决策点列表（响应格式见 13 号文档 9.1 节）
  - file: `src/multi_agent_system/api/admin_traces.py`
- [ ] 2.6 在 `src/multi_agent_system/api/routes.py` 注册上述 3 个路由，统一 `Depends(require_admin)`
  - file: `src/multi_agent_system/api/routes.py`（修改）
- [ ] 2.7 决策类型徽章颜色映射：routing 蓝、branching 紫、quality_gate 青、boundary 橙、tool_selection 灰、escalation 红
  - file: `web/src/pages/dev/SpanTreeView.tsx`
  - 设计依据：[13_开发人员工作台设计.md:127](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md)

## 3. D-02 Prompt 版本对比

- [ ] 3.1 新增 ORM `src/multi_agent_system/models/prompt_version.py`，字段按 13 号文档 8.1 节定义（agent_name / version / template / is_active / note / created_by / created_at + UNIQUE(agent_name, version)）
  - file: `src/multi_agent_system/models/prompt_version.py`（新增）
  - 设计依据：[13_开发人员工作台设计.md:279-292](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md)
- [ ] 3.2 在 `src/multi_agent_system/core/database.py` 注册 PromptVersion ORM 与表创建
  - file: `src/multi_agent_system/core/database.py`（修改）
- [ ] 3.3 新增 `src/multi_agent_system/services/prompt_manager.py`，实现 `PromptManager.get_active(agent_name)`：查 DB 激活版本，找不到降级到源码常量
  - file: `src/multi_agent_system/services/prompt_manager.py`（新增）
  - 设计依据：[13_开发人员工作台设计.md:206-218](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md)
- [ ] 3.4 改造 5 个 Agent 在 `__init__` 时调用 `PromptManager.get_active`，把硬编码 prompt 改为实例属性
  - file: `src/multi_agent_system/agents/ticket_intent.py`、`classifier.py`、`react_processor.py`、`reviewer.py`、`coordinator.py`（修改）
- [ ] 3.5 新增 `src/multi_agent_system/api/admin_prompts.py`，实现 3 个接口：
  - `GET /api/admin/prompts/{agent_name}/versions` 列表
  - `POST /api/admin/prompts/{agent_name}/versions` 新建（请求体：template, note）
  - `POST /api/admin/prompts/{agent_name}/versions/{version}/activate` 激活（事务：先 set false 再 set true，返回 requires_restart=true）
  - file: `src/multi_agent_system/api/admin_prompts.py`（新增）
- [ ] 3.6 激活接口实现"复制 + 新行"回滚路径：当请求体含 `rollback_from_version` 时，复制目标版本为新行（version 自增，note 标注"rollback to vX"）再激活
  - file: `src/multi_agent_system/api/admin_prompts.py`
  - 设计依据：[13_开发人员工作台设计.md:202-205](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md)
- [ ] 3.7 在 `routes.py` 注册上述路由
  - file: `src/multi_agent_system/api/routes.py`（修改）
- [ ] 3.8 新增前端 `web/src/pages/dev/PromptVersions.tsx`，左侧版本列表（★ 标激活）、右侧 diff 视图（react-diff-viewer-continued，支持 split/unified）
  - file: `web/src/pages/dev/PromptVersions.tsx`（新增）
  - 设计依据：[13_开发人员工作台设计.md:181-201](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md)
- [ ] 3.9 PromptVersions 顶部 Agent 选择器（5 个 Agent 下拉）+ 操作按钮（新建版本 / 激活 / 回滚）
  - file: `web/src/pages/dev/PromptVersions.tsx`
- [ ] 3.10 添加依赖 `react-diff-viewer-continued` 到 `web/package.json`
  - file: `web/package.json`（修改）

## 4. D-03 RAG 检索调试器

- [ ] 4.1 新增 `src/multi_agent_system/services/rag_debugger.py`，封装对 rag-service `/retrieve` `/rerank` 的 HTTP 透传调用，超时默认 10s
  - file: `src/multi_agent_system/services/rag_debugger.py`（新增）
  - 设计依据：[13_开发人员工作台设计.md:253-261](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md)
- [ ] 4.2 新增 `src/multi_agent_system/api/admin_rag.py`，实现 `POST /api/admin/rag/debug`，请求体 `{query, mode, top_k, rerank, collection}`，响应 `{retrieval_results, rerank_results, elapsed_ms}`（rerank_results 含 rank_change 字段）
  - file: `src/multi_agent_system/api/admin_rag.py`（新增）
  - 设计依据：[13_开发人员工作台设计.md:355-372](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md)
- [ ] 4.3 rank_change 计算：rerank_results 中每项的 `原始 rank - 重排后 rank`（正数表示上升）
  - file: `src/multi_agent_system/services/rag_debugger.py`
- [ ] 4.4 在 `routes.py` 注册 rag/debug 路由
  - file: `src/multi_agent_system/api/routes.py`（修改）
- [ ] 4.5 新增前端 `web/src/pages/dev/RagDebugger.tsx`，顶部查询表单（query / mode=vector|bm25|hybrid / top_k / rerank / collection），中部结果对比表（Rank / 片段ID / 原始分 / 重排分 / 变化 / metadata），底部 ChunkDetailSheet 展开原文
  - file: `web/src/pages/dev/RagDebugger.tsx`（新增）
  - 设计依据：[13_开发人员工作台设计.md:226-252](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md)

## 5. D-04 Token 控制台（复用 12 号文档方案）

- [ ] 5.1 后端：实现 `src/multi_agent_system/models/token_stats.py`（TokenDailyStats ORM）+ `services/quota_service.py`（AsyncSession 适配）+ `api/admin_stats.py`（4 个 stats 路由）+ `core/trace.py` 的 `_accumulate_token_daily_stats` UPSERT
  - file: 多个新增/修改
  - 设计依据：[12_Token成本控制台设计.md](../../docs/design-spec/01_正式设计/12_Token成本控制台设计.md)
  - 依赖：change 2 修复 `_finalize_span` 调用 `add_token_usage`
- [ ] 5.2 前端：从 `explore/admin-web/src/pages/TokenDashboard/` 拷贝 `index.tsx` / `dashboard-ui.tsx` / `dashboard-shared.ts` 到 `web/src/pages/dev/TokenDashboard/`
  - file: `web/src/pages/dev/TokenDashboard/`（新增 3 个文件）
- [ ] 5.3 修改 `web/src/api/admin.ts` 新增 4 个调用函数：`getTokenSummary` / `getDailyTokenStats` / `getHourlyTokenStats` / `getQuota`，baseURL 对齐主系统
  - file: `web/src/api/admin.ts`（修改）
  - 设计依据：[12_Token成本控制台设计.md:226-235](../../docs/design-spec/01_正式设计/12_Token成本控制台设计.md)
- [ ] 5.4 TokenDashboard 高耗 token trace 列表项加 "查看 trace" 链接，跳转 `/dev/traces?ticket_id=...&span_id=...`
  - file: `web/src/pages/dev/TokenDashboard/index.tsx`
  - 设计依据：[13_开发人员工作台设计.md:273](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md)

## 6. D-05 Agent 调用统计（依赖 change 2 token 累加修复）

- [ ] 6.1 新增 `src/multi_agent_system/services/agent_stats.py`，实现按 call_type 聚合 spans 表：调用次数（count）、平均耗时（avg duration）、成功率（status=success 占比）、错误率、总 token（依赖 spans.metadata.token_usage）
  - file: `src/multi_agent_system/services/agent_stats.py`（新增）
  - call_type 枚举对齐：[12_Token成本控制台设计.md:270-282](../../docs/design-spec/01_正式设计/12_Token成本控制台设计.md) 的 6 个值（intent / classify / process / review / coordinator / rag）
- [ ] 6.2 新增 `GET /api/admin/stats/agents`，查询参数：start_date / end_date / agent_name（可选），返回 `[{agent_name, call_count, avg_duration_ms, success_rate, error_rate, total_tokens}]`
  - file: `src/multi_agent_system/api/admin_stats.py`（修改，追加路由）
- [ ] 6.3 在 `routes.py` 注册（如 5.1 已注册 stats 前缀则仅追加子路由）
  - file: `src/multi_agent_system/api/routes.py`（修改）
- [ ] 6.4 新增前端 `web/src/pages/dev/AgentCallStats.tsx`，顶部时间范围选择 + 5 Agent 表格 + Recharts 柱状图（调用次数 / 平均耗时 / 成功率三视图切换）
  - file: `web/src/pages/dev/AgentCallStats.tsx`（新增）

## 7. D-06 服务健康检查

- [ ] 7.1 新增 `src/multi_agent_system/services/health_check.py`，实现 4 个探测函数：`check_rag_service` / `check_qdrant`（透传 rag-service） / `check_llm`（HEAD `/v1/models`） / `check_embedding`（HEAD `/v1/embeddings`），各自超时 2s，异常返回 `{status: "unhealthy", error: ...}`
  - file: `src/multi_agent_system/services/health_check.py`（新增）
- [ ] 7.2 新增 `GET /api/admin/health`，并发探测 4 个依赖，返回 `{services: [{name, status, latency_ms, error}], overall: "healthy"|"degraded"|"unhealthy"}`
  - file: `src/multi_agent_system/api/admin_health.py`（新增）
- [ ] 7.3 overall 状态规则：全部 healthy → healthy；部分 unhealthy → degraded；全部 unhealthy → unhealthy
  - file: `src/multi_agent_system/api/admin_health.py`
- [ ] 7.4 在 `routes.py` 注册 health 路由
  - file: `src/multi_agent_system/api/routes.py`（修改）
- [ ] 7.5 新增前端 `web/src/pages/dev/ServiceHealth.tsx`，4 张卡片（rag-service / Qdrant / LLM / Embedding），状态色阶（绿/黄/红），每张卡显示 latency_ms，自动每 30s 刷新（可手动刷新）
  - file: `web/src/pages/dev/ServiceHealth.tsx`（新增）

## 8. D-07 配额管理（复用 explore/quota_service.py）

- [ ] 8.1 后端 quota_service 在 5.1 中已实现（per-user 配额覆写 `users.token_monthly_limit` / `token_weekly_limit`），此处仅追加 `PATCH /api/admin/users/{user_id}/quota` 写接口
  - file: `src/multi_agent_system/api/admin_stats.py` 或新建 `admin_quota.py`（修改/新增）
  - 设计依据：[12_Token成本控制台设计.md:84-93](../../docs/design-spec/01_正式设计/12_Token成本控制台设计.md)
- [ ] 8.2 PATCH 接口请求体：`{token_monthly_limit?: int|null, token_weekly_limit?: int|null}`，null 表示走默认；admin 鉴权
  - file: 同上
- [ ] 8.3 前端在 TokenDashboard 配额面板（5.2 拷贝的 dashboard-ui.tsx 的 QuotaPanel 子组件）内嵌"调整配额"按钮，点击弹 Sheet 编辑两个字段
  - file: `web/src/pages/dev/TokenDashboard/dashboard-ui.tsx`（修改）
- [ ] 8.4 该接口同时被 A-04 用户管理（`web/src/pages/UserManagement.tsx`，将在 change 4 实现）复用，API 签名保持一致
  - 设计依据：[07_前端页面与交互设计.md:69-79](../../docs/design-spec/01_正式设计/07_前端页面与交互设计.md)

## 9. 单元测试与集成测试

- [ ] 9.1 `tests/services/test_prompt_manager.py`：版本激活事务回滚、降级到源码默认模板、回滚复制新行
- [ ] 9.2 `tests/api/test_admin_traces.py`：决策点抽取、span 树查询
- [ ] 9.3 `tests/api/test_admin_rag.py`：透传 rag-service 的请求/响应映射、rank_change 计算
- [ ] 9.4 `tests/api/test_admin_health.py`：4 个依赖各自 unhealthy 时的降级、overall 状态聚合
- [ ] 9.5 `tests/api/test_admin_stats_agents.py`：聚合查询正确性、按 call_type 分组
- [ ] 9.6 `tests/api/test_admin_quota.py`：per-user 配额覆写优先级、PATCH 接口权限
