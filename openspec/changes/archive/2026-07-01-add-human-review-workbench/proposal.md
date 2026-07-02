## Why

当前系统在三个场景下声称"转人工处理"，但实际只写入文字消息后直接归档完成，没有真正的人工审核环节：

- P0 / 投诉工单 `escalate` 节点（[graph.py:377](src/multi_agent_system/workflow/graph.py#L377)）写"已升级至人工处理"消息后仍走 notify → complete。
- AI 审核失败重试超限（[graph.py:491](src/multi_agent_system/workflow/graph.py#L491)）直接进入 `handle_failure → complete`，无人工兜底。
- 工作流异常（[routes.py:595](src/multi_agent_system/api/routes.py#L595)）仅写状态为 failed。

工单完成后用户主动反馈"不满意"也仅记录 `satisfied=0`，不触发任何复审流程。这些缺口让"AI 预审 + 人工终审"业务模型在毕设答辩时无法真实演示，AI 失败也缺少合理兜底。

## What Changes

- **新增** 工单状态 `pending_human_review`，标识工单已挂起等待人工决策。
- **新增** 工作流节点 `human_review_wait`（挂起并写入审核记录）与 `apply_human_decision`（接收决策并恢复执行）。
- **修改** 四个触发点的后继路由：
  - `escalate` 节点：`notify` → `human_review_wait`
  - `retry_check`（retry_count ≥ 3 分支）：`handle_failure` → `human_review_wait`
  - 工作流异常分支：直接 fail → 转入 `human_review_wait`（trigger_type=`error_fallback`）
  - 工单完成后的反馈接口：satisfied=0 时创建 `user_request` 类型审核记录
- **新增** 数据表 `human_reviews` 持久化审核记录（pending / decided）。
- **扩展** CoordinatorAgent 增加 `suggest_decision` 方法，为审核员提供辅助决策建议。
- **新增** API 端点 `/api/reviews/queue`、`/api/reviews/{ticket_id}`、`/api/reviews/{ticket_id}/decision`、`/api/reviews/stats`。
- **新增** WebSocket 事件 `review_requested` 与 `review_decided`，复用现有 `/ws/monitor` 端点。
- **新增** 前端审核工作台页面 `ReviewWorkbench.tsx`，双栏布局（队列 + 详情 + AI 建议卡 + 决策区）。
- **新增** Trace span 类型 `human_decision`，含 `ai_adopted` 字段以支撑 `ai_adoption_rate` 指标。
- **新增** 部分索引 `idx_tickets_pending` 加速待审核队列查询。

## Capabilities

### New Capabilities

- `ticket-human-review`: 工单人工审核生命周期 — 触发条件、状态机变更、审核决策、CoordinatorAgent 辅助建议、`human_reviews` 持久化、Trace 追踪。
- `reviewer-workbench`: 审核员工作台 — 待审核队列 API、决策提交 API、统计 API、WebSocket 事件、双栏前端工作台。

### Modified Capabilities

无。`openspec/specs/` 当前为空，本变更为首次引入 capability。

## Impact

- **后端代码**：
  - `src/multi_agent_system/workflow/graph.py`：新增 2 节点、修改 3 处条件路由
  - `src/multi_agent_system/workflow/state.py`：`TicketState` 字段无变化（决策数据通过 API 传入）
  - `src/multi_agent_system/agents/coordinator.py`：新增 `suggest_decision` 方法 + 降级实现
  - `src/multi_agent_system/models/ticket.py`：`TicketStatus` 枚举新增 `PENDING_HUMAN_REVIEW`
  - `src/multi_agent_system/api/routes.py`：新增 4 个 `/reviews/*` 端点 + 2 个 WebSocket 事件
  - `src/multi_agent_system/core/database.py`：新增 `human_reviews` 表 + 索引 + CRUD 方法
  - `src/multi_agent_system/core/trace.py`：支持 `human_decision` span 类型
- **前端代码**：
  - `web/src/pages/ReviewWorkbench.tsx`：新增
  - `web/src/types/index.ts`：新增 `HumanReview`、`ReviewDecision`、`TriggerType` 等类型
  - `web/src/api/`：新增 reviews API 客户端
  - `web/src/App.tsx` 或路由：注册 `/reviews` 路由
  - `web/src/components/`：新增 AI 建议卡、决策按钮组等组件
- **数据库迁移**：新增表 `human_reviews` + 索引；`tickets.status` 枚举扩展（SQLite 不强制枚举，仅文档约束）
- **依赖**：无新增第三方包
- **文档**：`docs/design-spec/` 下 10 篇文档已在 v1.0 同步更新（详见 [09_人工审核工作台设计.md](docs/design-spec/01_正式设计/09_人工审核工作台设计.md)）
- **测试**：新增工作流暂停/恢复测试、API 集成测试、前端交互测试；预估工时 6.5 天
