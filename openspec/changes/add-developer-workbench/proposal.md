## Why

v2.0 重构把"开发人员模块"明确列为 4 大角色模块之一（详见 [docs/design-spec/README.md](../../docs/design-spec/README.md) 与 [assets/system-module-architecture-v2-ascii.md](../../docs/design-spec/assets/system-module-architecture-v2-ascii.md)），用于回答答辩反馈中"模块要按角色分层 + 工作量不够"两点：

- 现状只有 [web/src/pages/AgentMonitor.tsx](../../web/src/pages/AgentMonitor.tsx) 一个实时面板，**离线调试能力缺失**：Trace 决策语义散落在 spans.metadata、Prompt 硬编码在 5 个 Agent 源文件里、RAG 调用结果不透明、Token 成本无聚合视图。
- [13_开发人员工作台设计.md](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md) 已规划 D-01 ~ D-07 共 7 个子模块，但仅有 D-04 在 [12_Token成本控制台设计.md](../../docs/design-spec/01_正式设计/12_Token成本控制台设计.md) 完成详细设计，其余 6 项未落地。
- 答辩现场需要"展示真实成本数字 + Trace 决策点 + Prompt 版本对比"三类素材，当前系统拿不出，论文成本/决策章节也无法取数。

本变更补齐这 7 个子模块，使开发人员模块从 1 个面板扩到 7 个子页，支撑论文 4 ~ 6 章的可观测与成本数据。

## What Changes

### 新增能力

- **`dev-trace-decision-tree`（D-01）**：前端 `web/src/pages/dev/SpanTreeView.tsx` 复用现有 `web/src/components/trace/` 组件（TraceGantt / SpanDetailSheet / DecisionTimeline / DecisionCard），后端新增 3 个 `/api/admin/traces/*` 接口。
- **`dev-prompt-versions`（D-02）**：新增 `prompt_versions` 表 + `PromptManager` 加载器 + 5 个 Agent 的 Prompt 模板版本管理 API（list / create / activate）+ 前端 `PromptVersions.tsx`（含 react-diff-viewer）。
- **`dev-rag-debugger`（D-03）**：新增 `/api/admin/rag/debug` 透传 rag-service 的 `/retrieve` `/rerank`，前端 `RagDebugger.tsx` 展示重排前后 top-k 对比。
- **`dev-agent-call-stats`（D-05）**：新增 `/api/admin/stats/agents` 聚合 5 Agent 的调用次数/平均耗时/成功率/错误率，前端 `AgentCallStats.tsx`。
- **`dev-service-health`（D-06）**：新增 `/api/admin/health` 探测 rag-service / Qdrant / LLM / Embedding 健康状态，前端 `ServiceHealth.tsx`。
- **`dev-quota-management`（D-07）**：复用 explore/quota_service.py，前端在 Token 控制台内嵌配额覆写 UI（与 A-04 用户管理共享 API）。

### 复用 / 重构

- D-04 Token 控制台直接复用 [12 号文档](../../docs/design-spec/01_正式设计/12_Token成本控制台设计.md) 已定义的方案（explore/admin-web 的 TokenDashboard 整体迁移 + 后端 UPSERT 重写），本变更不重复 spec，仅在 tasks 中引用并打通路由。
- `web/src/components/trace/` 现有组件不动，仅在 `SpanTreeView.tsx` 中扩展 `metadata.decision` 高亮与 `metadata.rag_stats` 渲染。

### 路由

新增 `/dev` 顶级路由，左侧导航 7 项，与现有 `AgentMonitor.tsx`（实时面板）并存（详见 13 号文档第 10.1 节）。

## Capabilities

### New Capabilities

- `dev-trace-decision-tree`
- `dev-prompt-versions`
- `dev-rag-debugger`
- `dev-agent-call-stats`
- `dev-service-health`
- `dev-quota-management`

### Modified Capabilities

无（D-04 Token 控制台不在本变更 spec 中，由 12 号文档独立承载）。

## Impact

### 依赖声明

本变更与同期的 Token 累加 / 决策点埋点修复（属于另一个 change，下文简称"change 2"）存在硬依赖：

- D-01 Trace 决策树渲染依赖 `spans.metadata.decision` 字段被正确埋点（5 个决策点：classify / route / process / review / retry_check）。若 change 2 未完成，Trace 决策树只能渲染节点骨架，决策五元组（trigger / options / selection / execution / reflection）为空。
- D-04 Token 控制台依赖 `traces.total_tokens` 在 `_finalize_span` 路径回写（13 号文档第 11 节 #1 P0 问题）。若 change 2 未完成，Token 聚合表全为 0。
- D-05 Agent 调用统计的 token 维度依赖同上。

**实施顺序**：change 2 先合并，再实施本变更的 D-01 / D-04 / D-05；D-02 / D-03 / D-06 / D-07 可独立并行。

### 后端代码

- `src/multi_agent_system/models/prompt_version.py`（新增 ORM）
- `src/multi_agent_system/services/prompt_manager.py`（新增 PromptManager + DB 加载降级）
- `src/multi_agent_system/services/rag_debugger.py`（新增，HTTP 透传到 rag-service）
- `src/multi_agent_system/services/health_check.py`（新增）
- `src/multi_agent_system/services/agent_stats.py`（新增，聚合 spans 表）
- `src/multi_agent_system/api/admin_traces.py`（新增 3 个 trace 接口）
- `src/multi_agent_system/api/admin_prompts.py`（新增 3 个 prompt 接口）
- `src/multi_agent_system/api/admin_rag.py`（新增 rag/debug 接口）
- `src/multi_agent_system/api/admin_stats.py`（新增 stats/agents 接口，含 Token 路由由 12 号文档定义）
- `src/multi_agent_system/api/admin_health.py`（新增 health 接口）
- `src/multi_agent_system/api/routes.py`（注册上述路由 + admin 鉴权）

### 前端代码

- `web/src/pages/DevConsole.tsx`（新增外壳 + 7 项左侧导航）
- `web/src/pages/dev/SpanTreeView.tsx`、`PromptVersions.tsx`、`RagDebugger.tsx`、`AgentCallStats.tsx`、`ServiceHealth.tsx`（新增 5 个子页）
- `web/src/pages/dev/TokenDashboard/`（按 12 号文档从 explore 整体迁移）
- `web/src/api/admin.ts`（新增 traces / prompts / rag / stats / health 调用函数）
- `web/src/App.tsx` 或路由配置：注册 `/dev/*` 路由
- `react-diff-viewer-continued`（新增依赖）

### 数据库

新增表 `prompt_versions`（schema 见 13 号文档 8.1 节）；`token_daily_stats` 与 `users.token_*_limit` 字段由 change 2 / 12 号文档落地，本变更不重复。

### 测试

- `tests/services/test_prompt_manager.py`：版本激活事务、降级到源码默认模板
- `tests/api/test_admin_traces.py`：决策点抽取接口
- `tests/api/test_admin_rag.py`：透传 rag-service 的请求/响应映射
- `tests/api/test_admin_health.py`：各服务不可用时的降级状态
- `tests/api/test_admin_stats_agents.py`：聚合查询正确性

### 工时预估

约 2-3 天（D-02 / D-03 / D-06 / D-07 可并行约 1.5 天，D-01 / D-05 依赖 change 2 合并后再约 1 天）。
