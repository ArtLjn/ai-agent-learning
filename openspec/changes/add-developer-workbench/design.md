## Context

开发人员模块是 v2.0 重构中"按角色分层"驱动新增的三大模块之一，定位于**离线调试与运维**场景，与现有 `AgentMonitor.tsx` 的"实时执行流"定位互补（详见 [13 号文档第 10.1 节](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md)）。

整体复用关系：

| 来源 | 复用方式 |
| --- | --- |
| `web/src/components/trace/`（已实现） | SpanTreeView 直接复用，仅扩展 `metadata.decision` 高亮 |
| `explore/backend/app/api/admin_trace.py` | 数据契约复用，按主系统 trace/span 模型重写 |
| `explore/admin-web/src/pages/TokenDashboard/`（948 行） | 整体迁移（详见 12 号文档） |
| `explore/backend/app/services/quota_service.py` | 复用核心逻辑，AsyncSession 适配 |

决策点五元组（trigger / options / selection / execution / reflection）作为 `spans.metadata.decision` 子结构落库，**零表结构变更**——这是本设计最关键的复用决策（13 号文档 4.1 节）。

## Goals / Non-Goals

### Goals

- 落地 D-01 ~ D-07 共 7 个子模块，前端入口统一为 `/dev` 顶级路由。
- D-02 Prompt 版本管理支持 list / create / activate / rollback，5 个 Agent 启动时从 DB 加载、找不到降级到源码常量。
- D-03 RAG 调试器透传 rag-service 的 `/retrieve` `/rerank`，主系统**不做检索逻辑**（11 号文档约束）。
- D-05 Agent 调用统计能按 call_type 分组聚合（与 12 号文档定义的 6 个 call_type 枚举对齐）。
- D-06 服务健康检查支持 4 个依赖：rag-service / Qdrant / LLM / Embedding。

### Non-Goals

- **不做** Reflection 反思 Agent（v1.1 已明确搁置，仅预留 reflection 字段位）。
- **不做** Prompt 在线编辑器（只支持粘贴模板文本，不做代码高亮与语法检查）。
- **不做** Prompt 自动 A/B 测试（需流量分流与统计显著性，超毕设）。
- **不做** RAG 评估指标（recall@k / MRR 等，留作 rag-service 论文章节）。
- **不做** 跨 trace 决策聚合大盘（如"近 7 天 classify 准确率"，超毕设）。
- **不做** Token 控制台的实时流式计费（LangGraph span 异步收尾已够）。

## 关键决策

### 决策 1：决策点数据落 `spans.metadata.decision`，不新增表

**选项**：

- A：新增 `decisions` 表（span_id / decision_type / trigger / options / selection）
- B：作为 `spans.metadata_` JSON 字段的 `decision` 子结构

**选择 B**。理由：

1. 决策点与 span 一一对应（每个决策 span 对应一条决策），独立表冗余。
2. JSON 字段已有（`SpanORM.metadata_`），零表结构变更、零迁移成本。
3. 查询模式简单（按 trace_id 拉 span 列表后内存过滤），不需要关系查询。
4. 13 号文档 4.1 节已明确此设计，本期执行即可。

**代价**：MySQL JSON 字段不能直接建索引，但 trace 维度查询足够（trace 数据量毕设范围内 < 10k 行）。

### 决策 2：Prompt 版本管理用"复制 + 新行"实现回滚，不修改历史行

13 号文档 5.3 节明确：回滚到 vX 时，**不直接修改 is_active**，而是复制 vX 的 template 为新行（version 自增），再激活新行。理由：

1. 历史版本完全 append-only，审计可追溯。
2. 避免出现"激活又被回滚"导致的中间状态。
3. 与 Prompt 版本对比 UI 的 diff 算法对齐（永远在两个具体版本间 diff）。

**代价**：版本号会有跳号（如 v3 → rollback to v1 → 实际是 v4 = v1 的副本）。`note` 字段标注 "rollback to v1" 即可。

### 决策 3：Agent 启动时一次性加载激活版本，不每次调用查 DB

5 个 Agent 在 `__init__` 时通过 `PromptManager.get_active(agent_name)` 读取并缓存到实例属性。激活版本切换后，**需要重启 workflow 进程**才生效。

**理由**：

1. 单次 DB 查询放在 Agent 生命周期起点，避免每条工单都查。
2. 答辩演示场景下，激活切换不频繁（论文实验阶段才频繁），重启可接受。
3. 不引入运行时热更新（需要 Pub/Sub 或定时刷新，超毕设）。

**API 设计**：激活接口返回 `requires_restart: true` 提示前端告知用户。

### 决策 4：RAG 调试器只透传，主系统不做检索

`/api/admin/rag/debug` 接口直接 HTTP 转发到 rag-service 的 `/retrieve` `/rerank`，主系统不做任何检索/重排逻辑。排名变化（rank_change）由主系统根据前后两次结果计算。

**理由**：

1. 11 号文档明确 RAG 全部归 rag-service，主系统只通过 `RAG Client` 调用。
2. 避免主系统侧实现检索逻辑造成"两套代码"。
3. 调试器需要的就是 rag-service 的真实输出，无需主系统加工。

### 决策 5：服务健康检查分级降级，不抛异常

`/api/admin/health` 探测每个依赖时，超时（默认 2s）或异常都返回 `status: "unhealthy"` + `error` 字段，**不抛 500**。

| 服务 | 探测方式 | unhealthy 时影响 |
| --- | --- | --- |
| rag-service | GET `/healthz` | D-03 RAG 调试器不可用，工单走降级路径 |
| Qdrant | GET `/healthz`（通过 rag-service 透传） | RAG 全停 |
| LLM | HEAD `/v1/models` 或最小 chat completion | 5 Agent 全停 |
| Embedding | HEAD `/v1/embeddings` | RAG 检索停 |

**理由**：开发人员看到 unhealthy 状态比看到 500 错误更有用，能直接定位是哪个依赖挂了。

### 决策 6：DevConsole 与 AgentMonitor 并存，不合并

13 号文档 10.1 节明确两者定位不同：AgentMonitor 是实时 WebSocket 推流（答辩演示用），DevConsole 是离线调试（论文分析用）。**保留两者**。

**代价**：导航会有两个相关入口，但通过命名（"实时监控" vs "开发工作台"）区分，可接受。

### 决策 7：配额管理 UI 复用 D-04 Token 控制台的位置，不单独建页

D-07 配额覆写 UI 嵌入 Token 控制台的"配额面板"区，不单独占左侧导航一项。理由：

1. 配额本质是 Token 维度的策略，与 Token 控制台数据源一致（`users.token_*_limit`）。
2. A-04 用户管理也会调用同一配额 API（用户管理表格内嵌配额调整），UI 入口有两个但 API 一致。
3. 7 项左侧导航已饱和，避免再加。

**API**：`PATCH /api/admin/users/{user_id}/quota`（同时被 D-07 和 A-04 调用，spec 归 D-07，A-04 复用）。
