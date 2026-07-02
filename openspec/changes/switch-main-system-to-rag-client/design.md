## Context

阶段 1+2（`add-rag-service-project` change）已完成 rag-service 独立项目搭建，提供 5 个核心 API。但主系统仍使用 v1.x 的 `tools/knowledge_search.py` 直连 Qdrant，双项目架构的论文价值无法体现。阶段 3（M3）需要完成主系统切换到 RAG Client 调用模式，同时修复 v1.1 遗留的 P0 埋点 bug，让 Token 成本控制台拿得到真实数据。

完整设计已沉淀到：

- [docs/design-spec/01_正式设计/11_RAG服务独立项目设计.md](../../docs/design-spec/01_正式设计/11_RAG服务独立项目设计.md) 第 13 章（与主系统集成）
- [docs/design-spec/01_正式设计/02_工单处理流程设计.md](../../docs/design-spec/01_正式设计/02_工单处理流程设计.md) 第 3.1 节（RAG 调用策略）
- [docs/design-spec/01_正式设计/13_开发人员工作台设计.md](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md) 第 11 章（P0 埋点修复方案）
- [docs/design-spec/01_正式设计/12_Token成本控制台设计.md](../../docs/design-spec/01_正式设计/12_Token成本控制台设计.md)（Token 模型与配额服务）

本 design.md 聚焦「为什么这样实现」的关键决策。

约束：

- 主系统位于 `/Users/ljn/Documents/demo/finished/ai-agent-learning/`，使用 FastAPI + LangGraph + SQLAlchemy 2.0 async + SQLite
- rag-service 已在阶段 1+2 完成，监听端口 8001
- 毕设范围：不做 RAG Client 内置缓存（默认关闭）、不做分布式熔断器
- 复用 explore/farm-manager 项目的 Token 统计模型代码

## Goals / Non-Goals

**Goals:**

- 主系统通过 `tools/rag_client.py` HTTP 调用 rag-service，替换内部 Qdrant 直连
- rag-service 不可达时主流程仍能完成（无知识增强分支）
- `traces.total_tokens` 真实累加，Token 成本控制台有数据
- 修复 v1.1 的 5 个 P0 决策点埋点问题，Trace 决策树完整可读
- Token 统计模型与配额服务落地，为开发人员模块前端提供 API
- 旧 `KnowledgeSearchTool` 保留作为降级备份（不删除）

**Non-Goals:**

- 不做 RAG Client 内置缓存（毕设范围默认关闭，展望章节讨论）
- 不做熔断器组件（毕设规模用简单的「连续 3 次失败 + 5 分钟冷却」替代）
- 不做 Token 控制台前端 UI（在阶段 4 开发人员工作台 change 中实现）
- 不做配额超限时的硬阻断（仅返回 warning，软提醒）
- 不删除 `tools/knowledge_search.py`（保留作为对照演示）
- 不做 Prompt 版本对比、RAG 调试器（在阶段 4 change 中实现）

## Decisions

### Decision 1: HTTP 客户端选择 — httpx vs aiohttp

**选择**：`httpx.AsyncClient`（主系统已通过 openai SDK 间接依赖 httpx）。

**理由**：

- 与主系统现有异步栈一致（FastAPI + asyncio）
- API 风格与 openai SDK 内部一致，团队熟悉度高
- 原生支持 timeout、retry、connection pool
- 主系统 `requirements.txt` 已包含 httpx，无需新增依赖

**替代方案考虑**：

- aiohttp：另一优秀异步 HTTP 客户端，但需要单独引入依赖
- requests + asyncio.to_thread：阻塞调用包装为协程，效率低

### Decision 2: 降级触发策略 — 简单计数 vs 熔断器

**选择**：简单「连续 3 次失败 + 5 分钟冷却」策略，不引入熔断器组件（如 pybreaker）。

**理由**：

- 毕设规模单实例部署，无分布式熔断需求
- 熔断器组件增加运维复杂度，状态机难以在论文中清楚阐述
- 简单策略可读性高：连续 3 次失败 → 标记 rag-service 不可用 → 5 分钟内直接走降级分支不发起调用 → 5 分钟后重试一次

**实现细节**：

- `RagClient` 内部维护 `_failure_count` 与 `_unavailable_until`（datetime）
- 每次调用前检查 `_unavailable_until`，若未过期直接抛 `RagServiceUnavailable`
- 调用成功时重置 `_failure_count=0`
- 10 秒超时 + 1 次重试（间隔 500ms），仍失败累加 `_failure_count`

**替代方案考虑**：

- 完全无降级：rag-service 抖动时主流程频繁失败，不可接受
- 完整熔断器（closed/open/half-open 三态）：毕设规模过度

### Decision 3: RagClient 生命周期 — 单例 vs 每次创建

**选择**：FastAPI app 启动时创建 `RagClient` 单例，注入到 `app.state.rag_client`，整个进程共享。

**理由**：

- httpx.AsyncClient 内部维护 connection pool，重复创建开销大
- `_failure_count` 与 `_unavailable_until` 状态需要进程内共享
- 单例便于测试时 mock（替换 app.state.rag_client 即可）

**注入方式**：

- `app.state.rag_client = RagClient(settings.rag_service_url, settings.rag_service_timeout, ...)`
- `processor_react.py` 通过依赖注入或全局获取 `app.state.rag_client`
- LangGraph 节点函数通过闭包捕获（与现有 knowledge_tool 注入方式一致，[app.py:83-88](src/multi_agent_system/api/app.py#L83)）

### Decision 4: Token 累加位置 — SpanContext.__aexit__ vs finish_trace

**选择**：在 `SpanContext.__aexit__`（[trace.py:90](src/multi_agent_system/core/trace.py#L90)）阶段调用 `add_token_usage`，并新增 `_accumulate_token_daily_stats` 同步写入 `token_daily_stats` 表。

**理由**：

- `__aexit__` 是 span 关闭的统一入口，所有 LLM 调用 span 都会经过
- `add_token_usage` 已存在（[trace.py:221](src/multi_agent_system/core/trace.py#L221)），仅需在 `__aexit__` 调用
- 双写（traces.total_tokens + token_daily_stats）保证两条统计路径都有数据：
  - `traces.total_tokens`：单 trace 粒度，用于 Trace 决策树展示
  - `token_daily_stats`：按 user_id + date 聚合，用于 Token 成本控制台

**累加条件**：

- 仅 `span_type=llm_call` 的 span 触发累加（其他 span 如 node/tool_call 没有 token 概念）
- span 的 `output_data.tokens`（如 `{prompt: 100, completion: 50, total: 150}`）非空时累加

**替代方案考虑**：

- 在 `finish_trace` 累加：trace 关闭时一次性累加所有 span 的 token，但需要在 finish_trace 时遍历所有 span，且 trace 异常中断时无法累加
- 在每个 Agent 内手动累加：侵入性强，容易遗漏

### Decision 5: Token 模型适配 — 去 farm_id 加 ticket_id

**选择**：从 explore/farm-manager 项目复用 Token 统计模型代码，去除 farm_id 字段，新增 ticket_id 字段关联到工单。

**理由**：

- farm-manager 已有完整的 TokenDailyStats/TokenHourlyStats/QuotaInfo 模型与 API
- 主系统场景下，Token 消耗的「业务对象」是工单而非农场
- ticket_id 关联便于按工单维度查询「这个工单花了多少 token」

**字段差异**：

| 字段 | farm-manager | ai-agent-learning |
| --- | --- | --- |
| `farm_id` | 有 | 无 |
| `ticket_id` | 无 | 有（可选，trace 级别必填） |
| `user_id` | 有 | 有 |
| `model_name` | 有 | 有 |
| `prompt_tokens` | 有 | 有 |
| `completion_tokens` | 有 | 有 |
| `total_tokens` | 有 | 有 |
| `date` | 有 | 有 |

### Decision 6: 配额服务策略 — 软提醒 vs 硬阻断

**选择**：软提醒模式 — 配额超限时仅返回 warning，不阻断 LLM 调用。

**理由**：

- 硬阻断会导致工单处理流程中断，违反「AI 失败有人工兜底」原则
- 软提醒让开发人员模块前端展示「配额 90% 已用」黄色预警，运维侧决策是否调整
- 毕设场景下，配额主要用于论文实验数据采集，不是生产级成本控制

**配额检查时机**：

- `QuotaService.check(user_id)` 返回 `{monthly_used, monthly_limit, weekly_used, weekly_limit, status: "ok"|"warning"|"exceeded"}`
- 在 `_finalize_span` 写入 token_daily_stats 后异步触发配额检查（不阻塞主流程）
- 超限时写入 `quota_warnings` 表（可选），并推送给开发人员模块前端（WebSocket）

### Decision 7: 决策点 P0 修复 — 独立 span vs 子结构嵌套

**选择**：根据决策点性质分别处理：

- **Memory 加载**：独立 `span_type=node` span，命名 `memory_load`
- **RAG 检索 hit_count/top_score**：写入 process span 的 `metadata.rag_stats`（已有规划，本次修复确保实际写入）
- **retry_check/should_review 决策**：在对应 node span 的 `output_data.decision` 子结构中结构化（如 `{decision: "retry", reason: "review_score_below_threshold", retry_count: 2, max_retries: 3}`）
- **classify span reason**：在 classify node span 的 `output_data.reason` 字段
- **traces.total_tokens**：见 Decision 4

**理由**：

- Memory 加载是独立耗时操作，单独 span 便于分析延迟
- RAG 检索统计是 process span 的子信息，嵌套在 metadata 更自然
- 决策点结构化而非扁平字段，便于前端 Trace 决策树递归渲染
- classify 的 reason 已有字段位，本次仅补全写入逻辑

### Decision 8: 旧 KnowledgeSearchTool 处置 — 保留作为降级备份

**选择**：保留 `tools/knowledge_search.py` 与 `tools/knowledge_tool_adapter.py` 不删除，通过配置项 `legacy_knowledge_tool_enabled`（默认 false）控制是否注册。

**理由**：

- 答辩演示时可对照「v1.x 直连 Qdrant vs v2.0 HTTP 调用 rag-service」
- 万一 rag-service 项目迁移 PDF 解析代码受阻，主系统可临时切回旧工具作为降级备份
- 删除代码不可逆，保留符合「毕设范围控制」原则
- 配置项默认关闭，不影响新架构的主流程

## Risks / Trade-offs

- **[风险] rag-service 网络抖动导致主流程延迟** → 缓解：10 秒超时 + 1 次重试 + 连续 3 次失败进入 5 分钟冷却；论文实验记录延迟数据
- **[风险] Token 累加在 SQLite 高并发写入下性能问题** → 缓解：毕设规模单进程 asyncio，无真正并发写；token_daily_stats 按日聚合并发量低
- **[风险] 旧 KnowledgeSearchTool 与新 RagClient 并存导致代码混乱** → 缓解：通过配置项互斥注册；README 明确说明 v2.0 默认走 RagClient
- **[风险] P0 埋点修复涉及多 Agent 文件改动，回归风险** → 缓解：每个 P0 修复配套单元测试；阶段 3 结束跑全量回归
- **[折中] 不做硬配额阻断** → 论文展望章节讨论生产场景的硬阻断设计
- **[折中] 不删除旧工具** → 代码冗余但保留对照演示能力
- **[折中] 不做 RAG Client 缓存** → 同 query 5 分钟内复用结果可在 v3.0 引入

## Migration Plan

**部署步骤**：

1. 实施 `tools/rag_client.py` 与降级策略
2. 修改 `processor_react.py`、`processor_legacy.py`、`coordinator.py` 的 RAG 调用点
3. 修改 `app.py` 启动初始化（移除 KnowledgeSearchTool，注入 RagClient）
4. 实施 Token 累加修复（`SpanContext.__aexit__` 调用 `add_token_usage`）
5. 新增 `models/token_stats.py`、`services/quota_service.py`、`api/admin_stats.py`
6. 数据库 schema 扩展（`token_daily_stats` 表 + users 表字段）
7. 修复 5 个 P0 决策点埋点
8. 测试与回归

**回滚策略**：

- `config.yaml` 设置 `legacy_knowledge_tool_enabled=true` 切回旧工具
- `RagClient` 失败时自动走降级分支，主流程不中断
- Token 累加失败不阻塞 span 关闭，仅记 warning 日志

**兼容性**：

- API 完全向后兼容（仅新增 `/admin/tokens/*` 端点，不修改现有端点）
- 数据库通过 `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN IF NOT EXISTS`（SQLite 兼容写法）自动迁移
- 前端旧版本不访问新端点不受影响

## Open Questions

无未决问题。如实现过程中遇到以下情况需重新讨论：

- LangGraph 节点函数获取 `app.state.rag_client` 方式与现有 knowledge_tool 注入不一致 → 统一为通过 state 字段传递
- SQLite 不支持 `ALTER TABLE ADD COLUMN IF NOT EXISTS` → 改为启动时检查列存在性后执行 ALTER
- token_daily_stats 表数据量过大 → 毕设规模无问题；展望章节讨论按月分表
