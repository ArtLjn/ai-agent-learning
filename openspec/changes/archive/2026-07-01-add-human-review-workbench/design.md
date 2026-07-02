## Context

当前 `ai-agent-learning` 项目（毕设阶段）已实现完整的多 Agent 工单处理闭环：FastAPI + LangGraph + React + SQLite + Qdrant，含 4 个 Agent、6 个前端页面、Trace 系统、WebSocket 推送。但人工审核环节存在"假升级"问题（详见 proposal.md）。

详细背景与 14 章完整设计已沉淀到 [docs/design-spec/01_正式设计/09_人工审核工作台设计.md](../../docs/design-spec/01_正式设计/09_人工审核工作台设计.md)。本 design.md 聚焦"为什么这样实现"的关键技术决策，避免重复 design-spec 的内容。

约束：

- 单 SQLite 数据库，无外部消息队列
- 单进程 FastAPI + asyncio.create_task 后台执行
- LangGraph 已编译状态图，未启用 checkpointer
- 现有 6 个前端页面使用 React 18 + TypeScript + Vite + Tailwind

## Goals / Non-Goals

**Goals:**

- 让"升级人工"成为可演示的真实业务流程，修复答辩逻辑漏洞
- 让 AI 审核失败有合理兜底，避免直接返回低质量结果
- 引入 CoordinatorAgent 辅助决策建议，体现 Agent + 人协同
- 所有审核动作可被持久化、追踪、统计，为论文提供数据
- 复用现有 Trace、WebSocket、DB 基础设施，最小化改动

**Non-Goals:**

- 审核员账户体系与登录（毕设展望）
- 技能组分配、SLA 超时、认领锁定（毕设展望）
- 多人会签 / 工单转派链路（毕设展望）
- 真实邮件 / 短信通知审核员（毕设展望）
- 引入 LangGraph checkpointer 或外部任务队列（架构升级，不在本次范围）

## Decisions

### Decision 1: 暂停节点实现 — 持久化 + 重新触发 vs LangGraph interrupt

**选择**：持久化 + 重新触发（不使用 LangGraph interrupt）。

**理由**：

- LangGraph interrupt 需要启用 checkpointer（如 `MemorySaver` 或 `SqliteSaver`），引入新的并发与状态管理复杂度
- 当前系统后台执行模式为 `asyncio.create_task`，每次 `astream` 都是独立调用，无 checkpointer 依赖
- 持久化方案：`human_review_wait` 节点写入 `pending` 记录后正常返回 END，结束本次执行；审核 API 收到决策后，**构造新的初始状态并从 `apply_human_decision` 节点开始执行新的工作流实例**
- 这种做法与现有 `_run_workflow` 函数（[routes.py:517](../../src/multi_agent_system/api/routes.py#L517)）的"后台执行 + 节点流式推送"模式完全一致，无需新增基础设施

**替代方案考虑**：

- LangGraph interrupt + checkpointer：更"正统"，但需要为现有工作流引入 checkpointer 配置、调整 `_run_workflow`、处理恢复时的状态反序列化，工作量增加约 2 天，且毕设阶段无法体现差异化价值
- 数据库轮询：审核员界面定期轮询 pending 工单 — 已通过 WebSocket 实时推送替代

### Decision 2: 工作流恢复 — 独立入口 vs 入口函数复用

**选择**：为 `apply_human_decision` 提供独立的 workflow 入口函数 `resume_from_human_decision(ticket_id, decision, ...)`，复用现有 `build_ticket_graph` 编译的图实例。

**理由**：

- 现有 `build_ticket_graph` 是单例编译，恢复时不能重新构建
- 新增独立入口函数清晰表达"恢复"语义，便于测试
- 入口函数内部通过 `workflow.astream(initial_state, {"start_node": "apply_human_decision"})` 控制起始节点（LangGraph 支持 `start_node` 参数）

**替代方案考虑**：

- 拆分两个独立的 LangGraph 图：维护成本高，状态共享复杂
- 通过 API 直接修改 tickets 表 + 调用单个节点函数：绕过工作流追踪，丢失 span 记录

### Decision 3: 数据模型 — 单表 vs 多表

**选择**：单表 `human_reviews` 持久化所有审核记录。

**理由**：

- 一对多关系：一个 ticket 可能有多次审核（如 reprocess 后再次失败）
- 单表通过 `created_at` 排序即可还原审核时间线，无需 join
- 简化毕设阶段的查询与统计

字段设计要点：

- `review_id` 格式 `HR-<trace_id>`，与现有 `TK-<trace_id>` 工单 ID 风格一致
- `ai_suggestion` 存 JSON 字符串（SQLite 无 JSON 类型，复用 `references_json` 模式）
- 部分索引 `idx_tickets_pending` 仅索引 `status='pending_human_review'` 行，加速队列查询

### Decision 4: CoordinatorAgent 扩展点 — 新增方法 vs 新 Agent

**选择**：在 `CoordinatorAgent` 新增 `suggest_decision` 方法，不创建新 Agent。

**理由**：

- CoordinatorAgent 现有职责就是"全局协调 + 异常兜底"，与"辅助人工决策"语义一致
- 避免新增 Agent 类需要注入到工作流图、增加测试覆盖
- 复用 CoordinatorAgent 已有的 `CachedLLMClient`、降级装饰器、fallback 注册机制

方法签名：

```python
async def suggest_decision(
    self,
    ticket_id: str,
    trigger_type: str,
    trigger_reason: str,
    processing_result: str | None,
    review_score: float | None,
) -> dict
```

返回 `{recommended_decision, confidence, reasoning, key_concerns}`，LLM 不可用时按 trigger_type 走规则降级。

### Decision 5: WebSocket 事件 — 复用 /ws/monitor vs 新增端点

**选择**：复用现有 `/ws/monitor` 全局监控端点，扩展事件类型。

**理由**：

- 现有 `_broadcast_ticket_update` 函数已支持任意 payload 推送，新增事件类型只需扩展 payload `type` 字段
- 审核工作台与全局监控使用相同连接，避免管理两套连接池
- 毕设阶段单一审核员场景，无需按审核员分通道

新增事件：

- `review_requested`：工单挂起时广播
- `review_decided`：决策提交后广播

### Decision 6: 前端架构 — 单页面 vs 拆分组件

**选择**：单页面 `ReviewWorkbench.tsx` + 局部子组件抽取。

**理由**：

- 整个审核工作台是单一交互场景，无路由跳转需求
- 拆出 `ReviewQueueItem`、`AIAssistanceCard`、`DecisionPanel` 等子组件便于复用与测试
- 遵循前端规范（[frontend-style.md](../../.claude/rules/frontend-style.md)）：单文件超过 150 行考虑拆分

子组件计划：

- `ReviewQueue.tsx`：左栏队列
- `ReviewDetailPanel.tsx`：右栏详情
- `AIAssistanceCard.tsx`：AI 建议卡片
- `DecisionPanel.tsx`：决策按钮组 + 改写框 + 理由框

### Decision 7: Trace span 集成 — 通用 span 类型 vs 专用 span

**选择**：在现有 `span_type` 枚举中新增 `human_decision` 类型，不创建专用 span 表。

**理由**：

- 现有 `spans` 表 schema（[05_数据存储设计.md](../../docs/design-spec/01_正式设计/05_数据存储设计.md)）通过 `span_type` 区分节点 / LLM 调用 / 工具调用，新增类型无需 schema 变更
- span 的 `input_data` / `output_data` / `metadata` JSON 字段足够灵活
- `human_decision` span 的 `duration` 字段记录"从挂起到决策"的真实等待时间，是论文重要数据

`ai_adopted` 字段位置：放在 `output_data.ai_adopted`，便于后续聚合查询。

### Decision 8: 错误处理与幂等性

**选择**：

- 工单状态校验作为决策 API 的强约束（409 `TICKET_NOT_PENDING`）
- 决策记录一旦 `decided` 不可修改，但可触发新的 `user_request` 审核
- 工作流恢复失败时回滚 `human_reviews.status` 为 `pending`

**理由**：

- 防止审核员并发提交导致工作流多次执行
- 保留审计轨迹完整性
- 工作流恢复失败不丢失待审核状态，审核员可重新提交

## Risks / Trade-offs

- **[风险] 持久化 + 重新触发方案在 LangGraph 内部不"原生"** → 缓解：在 design-spec 第 3.2 节明确说明此设计选择，论文答辩时主动阐述取舍
- **[风险] 部分索引 `idx_tickets_pending` 仅 SQLite 3.8+ 支持** → 缓解：检查部署环境 SQLite 版本，回退方案为普通索引
- **[风险] CoordinatorAgent 辅助建议质量影响 `ai_adoption_rate` 指标** → 缓解：prompt 设计强调"保守建议"原则，置信度 < 0.5 时不强行推荐
- **[风险] 审核员并发提交同一工单** → 缓解：决策 API 通过 `WHERE status='pending_human_review'` 的乐观更新保证幂等，第二个请求收到 409
- **[风险] 工作流恢复后 trace 链路断裂** → 缓解：恢复时复用原 trace_id，新 span 写入同一 trace，前端可看到完整时间线
- **[折中] 不实现审核员账户体系** → 通过 `reviewer_id` 字段（前端传入）标识，论文展望章节说明
- **[折中] 不实现 SLA 超时** → 等待时长仅做前端视觉提示（红色标记），无后端超时机制
- **[折中] 不实现审核员认领锁定** → 多个审核员可能同时打开同一工单，第二个提交收到 409 后前端刷新

## Migration Plan

**部署步骤**：

1. 应用启动时 `core/database.py` 的 schema 初始化自动创建 `human_reviews` 表与索引（SQLite `CREATE TABLE IF NOT EXISTS`）
2. `tickets.status` 枚举扩展通过 Pydantic model 更新（`TicketStatus.PENDING_HUMAN_REVIEW`），SQLite 列无类型约束，无需 DDL 变更
3. 现有 `escalate` 节点的下游从 `notify` 改为 `human_review_wait`，存量已 completed 工单不受影响
4. 现有 `retry_check` 的 `handle_failure` 分支改为 `human_review_wait`，存量已 failed 工单不受影响
5. 前端新增 `/reviews` 路由，原有 6 个页面无变更

**回滚策略**：

- 工作流图改造可通过 Git 回退恢复
- 数据库新增表可通过 `DROP TABLE human_reviews` 移除
- 已挂起待审核的工单如需回滚，手动执行 SQL：`UPDATE tickets SET status='failed' WHERE status='pending_human_review'`

**兼容性**：

- API 完全向后兼容（仅新增端点，不修改现有端点）
- WebSocket 客户端不感知新事件类型时静默忽略（payload `type` 字段未识别）
- 前端旧版本不访问 `/reviews` 路由不受影响

## Open Questions

无未决问题。所有关键决策已在 design-spec 第 1-14 章与本文档第 1-8 节明确。如实现过程中遇到以下情况需重新讨论：

- LangGraph `start_node` 参数在当前版本（langgraph 0.x）不可用 → 改为构造仅包含 `apply_human_decision → 后继节点` 的子图
- `ai_suggestion` JSON 在 SQLite TEXT 列过长（> 1MB） → 改为单独表存储
