## 1. 数据模型与基础设施

- [ ] 1.1 在 `src/multi_agent_system/models/ticket.py` 的 `TicketStatus` 枚举新增 `PENDING_HUMAN_REVIEW = "pending_human_review"`
- [ ] 1.2 在 `src/multi_agent_system/core/database.py` 的 `_SCHEMA_SQL` 新增 `human_reviews` 表（含全部 12 个字段）
- [ ] 1.3 在 `core/database.py` 新增索引 `idx_hr_status`、`idx_hr_ticket`、`idx_hr_trigger`、`idx_hr_reviewer`
- [ ] 1.4 在 `core/database.py` 新增部分索引 `idx_tickets_pending`（`WHERE status='pending_human_review'`），并验证 SQLite 版本兼容
- [ ] 1.5 新增 `src/multi_agent_system/models/review.py`：定义 `HumanReview`、`TriggerType`、`ReviewDecision`、`ReviewStatus` 等 Pydantic 模型
- [ ] 1.6 在 `core/database.py` 新增 `human_reviews` 表的 CRUD 方法：`create_pending_review`、`get_pending_review_by_ticket`、`update_review_decision`、`list_pending_reviews`、`list_reviews_by_ticket`、`get_review_stats`
- [ ] 1.7 运行 `pytest tests/core/test_database.py -k human_review` 验证表与索引创建成功

## 2. CoordinatorAgent 扩展

- [ ] 2.1 在 `src/multi_agent_system/agents/coordinator.py` 新增 `_SUGGEST_DECISION_PROMPT` 模板（含 4 条决策原则与严格 JSON 输出格式）
- [ ] 2.2 实现 `CoordinatorAgent.suggest_decision()` 方法：调用 `self.client` 获取 LLM 输出并解析 JSON
- [ ] 2.3 实现 `_fallback_suggest_decision()` 静态方法：按 trigger_type 走规则降级（escalate→reprocess、review_failed→rewrite、其他→approve）
- [ ] 2.4 在文件末尾 `fallback_registry.register(...)` 注册新降级方法
- [ ] 2.5 在 `tests/agents/test_coordinator.py` 新增 `TestSuggestDecision` 类，覆盖 LLM 正常、LLM 异常、JSON 解析失败、4 种 trigger_type 降级 5 个场景

## 3. 工作流改造

- [ ] 3.1 在 `src/multi_agent_system/workflow/state.py` 的 `TicketState` 新增字段（如有需要），保持向后兼容
- [ ] 3.2 在 `workflow/graph.py` 新增 `human_review_wait(state)` 节点函数：调用 CoordinatorAgent.suggest_decision → 写入 pending 记录 → 更新 ticket 状态 → 创建 human_decision span（pending 状态）→ 广播 review_requested 事件 → 返回 END 标记
- [ ] 3.3 在 `workflow/graph.py` 新增 `apply_human_decision(state, decision, decision_reason, rewritten_result, reviewer_id)` 节点函数：按 decision 矩阵路由，更新 human_reviews 行，完成 span，广播 review_decided 事件
- [ ] 3.4 修改 `escalate` 节点的下游：`graph.add_edge("escalate", "notify")` → `graph.add_edge("escalate", "human_review_wait")`
- [ ] 3.5 修改 `retry_decision` 函数：`retry_count >= max_retries` 分支返回 `"human_review_wait"` 而非 `"handle_failure"`
- [ ] 3.6 修改 `_run_workflow` 异常处理逻辑：捕获异常后创建 `error_fallback` 类型 pending 审核记录，状态置为 `pending_human_review` 而非 `failed`
- [ ] 3.7 在 `build_ticket_graph()` 注册两个新节点；保持现有节点注册不变
- [ ] 3.8 实现 `resume_from_human_decision(ticket_id, decision, ...)` 入口函数：构造仅从 `apply_human_decision` 开始的子图或使用 `start_node` 参数
- [ ] 3.9 编写 `tests/workflow/test_graph.py` 新增测试类：`TestHumanReviewWaitNode`、`TestApplyHumanDecisionNode`，覆盖 4 种触发 + 4 种决策共 8 个核心场景

## 4. API 端点

- [ ] 4.1 在 `src/multi_agent_system/api/routes.py` 新增 `GET /reviews/queue` 端点：支持 `trigger_type`/`category`/`priority`/`limit`/`offset` 参数，返回排序后的队列
- [ ] 4.2 新增 `GET /reviews/{ticket_id}` 端点：返回工单完整审核上下文（原文、AI 结果、trace 摘要、当前 ai_suggestion、历史审核列表）
- [ ] 4.3 新增 `POST /reviews/{ticket_id}/decision` 端点：校验状态与必填字段 → 更新 human_reviews → 调用 `resume_from_human_decision` → 广播 WebSocket 事件
- [ ] 4.4 实现错误码：404 `TICKET_NOT_FOUND`、409 `TICKET_NOT_PENDING`、400 `REWRITE_RESULT_REQUIRED`、400 `DECISION_REASON_REQUIRED`
- [ ] 4.5 新增 `GET /reviews/stats` 端点：返回 `pending_count`/`decided_today`/`decision_distribution`/`avg_decision_seconds`/`ai_adoption_rate`
- [ ] 4.6 修改现有 `POST /tickets/{ticket_id}/feedback` 端点：satisfied=false 时自动创建 `user_request` 类型 pending 审核记录
- [ ] 4.7 编写 `tests/api/test_reviews.py`：覆盖队列查询、详情、4 种决策、错误码、统计、幂等性共 12+ 测试用例

## 5. WebSocket 事件

- [ ] 5.1 在 `_broadcast_ticket_update` 函数旁新增 `_broadcast_review_event(event_type, ticket_id, payload)` 辅助函数
- [ ] 5.2 在 `human_review_wait` 节点调用 `_broadcast_review_event("review_requested", ...)`
- [ ] 5.3 在 `apply_human_decision` 节点调用 `_broadcast_review_event("review_decided", ...)`
- [ ] 5.4 在 `docs/design-spec/03_接口协议/02_WebSocket实时推送协议.md` 补充两种新事件类型的 payload schema
- [ ] 5.5 编写 WebSocket 集成测试：验证事件正确广播到 `/ws/monitor` 连接

## 6. Trace 集成

- [ ] 6.1 在 `src/multi_agent_system/core/trace.py` 的 `span_type` 验证逻辑新增 `human_decision`
- [ ] 6.2 在 `human_review_wait` 节点创建 pending 状态的 `human_decision` span
- [ ] 6.3 在 `apply_human_decision` 节点更新 span 为 decided 状态，写入 `output_data.decision`、`output_data.reviewer_id`、`output_data.ai_adopted`、`duration`
- [ ] 6.4 实现 `ai_adopted` 计算：比较 `decision` 与 `ai_suggestion.recommended_decision`，一致为 true
- [ ] 6.5 验证 trace 接口 `GET /tickets/{ticket_id}/trace` 能正确返回 `human_decision` span

## 7. 前端类型与 API 客户端

- [ ] 7.1 在 `web/src/types/index.ts` 新增类型：`TriggerType`、`ReviewDecision`、`HumanReview`、`ReviewQueueItem`、`ReviewDetail`、`ReviewStats`、`AISuggestion`、`ReviewRequestedEvent`、`ReviewDecidedEvent`
- [ ] 7.2 在 `web/src/api/` 新增 `reviews.ts`：实现 `getReviewQueue`、`getReviewDetail`、`submitDecision`、`getReviewStats` 函数
- [ ] 7.3 在 WebSocket 客户端新增 `review_requested` 与 `review_decided` 事件处理 hook

## 8. 前端审核工作台页面

- [ ] 8.1 创建 `web/src/pages/ReviewWorkbench.tsx` 主页面骨架（双栏布局）
- [ ] 8.2 实现 `web/src/components/reviews/ReviewQueue.tsx` 左栏：筛选器 + 队列列表，每项显示工单摘要/trigger 徽章/优先级色块/AI 建议摘要/等待时长
- [ ] 8.3 实现 `web/src/components/reviews/ReviewQueueItem.tsx`：单个队列项，等待 >30 分钟显示"超时"标记
- [ ] 8.4 实现 `web/src/components/reviews/ReviewDetailPanel.tsx` 右栏顶部：工单原文 + 分类徽章 + 优先级
- [ ] 8.5 实现 `web/src/components/reviews/AIProcessingResultCard.tsx`：展示 AI 处理结果
- [ ] 8.6 实现 `web/src/components/reviews/TraceTimeline.tsx`：复用现有 trace 展示组件，支持 human_decision span 高亮
- [ ] 8.7 实现 `web/src/components/reviews/AIAssistanceCard.tsx`：展示 AI 建议，置信度 >0.7 高亮
- [ ] 8.8 实现 `web/src/components/reviews/DecisionPanel.tsx`：4 个决策按钮（绿/蓝/黄/红）+ 改写文本框（仅 rewrite 展开）+ 必填理由框
- [ ] 8.9 在 `web/src/App.tsx` 注册 `/reviews` 路由
- [ ] 8.10 在导航栏（Sidebar/Navbar）新增"审核工作台"入口
- [ ] 8.11 实现 WebSocket 事件处理：收到 `review_requested` 时队列顶部插入新项 + toast；收到 `review_decided` 且为当前选中时刷新为已决策状态

## 9. 端到端测试与集成

- [ ] 9.1 编写端到端测试：投诉工单 → escalate → human_review_wait → API 决策 approve → notify → complete 全流程
- [ ] 9.2 编写端到端测试：review 失败 3 次 → human_review_wait → 决策 rewrite → notify → complete
- [ ] 9.3 编写端到端测试：review 失败 3 次 → human_review_wait → 决策 reprocess → process → review → 通过
- [ ] 9.4 编写端到端测试：工作流异常 → error_fallback 触发 → 决策 approve → 恢复
- [ ] 9.5 编写端到端测试：completed 工单 + satisfied=false → user_request 审核
- [ ] 9.6 验证 trace 接口能完整展示"AI → 暂停 → 人工 → 恢复"全过程
- [ ] 9.7 验证 `ai_adoption_rate` 统计正确（手工触发多种决策后查询 stats）

## 10. 文档与配置

- [ ] 10.1 在 `config.yaml` 与 `config.py` 新增审核相关配置项：`review_timeout_threshold`（默认 1800 秒）、`ai_suggestion_high_confidence_threshold`（默认 0.7）
- [ ] 10.2 验证 `docs/design-spec/01_正式设计/09_人工审核工作台设计.md` 与最终实现一致（如有偏差回写文档）
- [ ] 10.3 更新 `docs/design-spec/05_系统测试/02_核心测试用例.md` 补充人工审核相关测试用例
- [ ] 10.4 在 `README.md` 项目演示章节新增审核工作台截图占位（实现后补充）
- [ ] 10.5 运行 `bash scripts/check-doc-freshness.sh`（如脚本存在）验证文档与代码同步

## 11. 上线前验证

- [ ] 11.1 运行 `pytest tests/` 全量测试通过
- [ ] 11.2 运行 `ruff check src/ tests/` 无 lint 错误
- [ ] 11.3 启动后端 + 前端，手工验证 4 种触发场景都能进入审核队列
- [ ] 11.4 手工验证 4 种决策都能正确路由到 notify/process/complete
- [ ] 11.5 手工验证 WebSocket 事件实时推送到工作台
- [ ] 11.6 验证数据库迁移：从空库启动 → 表与索引自动创建 → 测试数据可写入查询
- [ ] 11.7 性能验证：构造 100 条 pending 工单，验证 `GET /reviews/queue` 响应时间 < 200ms
