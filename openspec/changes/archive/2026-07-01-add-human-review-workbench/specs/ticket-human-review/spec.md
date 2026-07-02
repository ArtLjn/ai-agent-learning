## ADDED Requirements

### Requirement: 工单进入人工审核的触发条件

系统 SHALL 在以下四种场景下将工单状态置为 `pending_human_review` 并持久化一条 `human_reviews` 记录（status=`pending`）：

- **escalate 触发**：路由判断为 `complaint` 或优先级为 `P0`，进入 `human_review_wait` 节点。
- **review_failed 触发**：`review` 节点评分低于阈值且 `retry_count` 已达上限（默认 3 次）。
- **error_fallback 触发**：工作流执行过程中抛出未恢复异常。
- **user_request 触发**：工单 `completed` 状态下用户反馈不满意（`satisfied=0`）。

每次触发时 `trigger_type` 字段 MUST 准确反映触发来源。

#### Scenario: 投诉类工单进入人工审核

- **WHEN** 用户提交内容为"我要投诉你们的服务"的工单
- **THEN** ClassifierAgent 输出 `category=complaint`
- **AND** 路由节点将其送入 `escalate`
- **AND** `escalate` 之后 MUST 进入 `human_review_wait` 节点（不再直连 notify）
- **AND** `tickets.status` 被更新为 `pending_human_review`
- **AND** `human_reviews` 表新增一行 `trigger_type=escalate`、`status=pending` 的记录

#### Scenario: P0 优先级工单进入人工审核

- **WHEN** ClassifierAgent 输出 `priority=P0` 的工单走完 `escalate` 节点
- **THEN** 该工单 MUST 进入 `human_review_wait`，与投诉类走相同流程

#### Scenario: AI 审核失败重试超限进入人工审核

- **WHEN** `review` 评分低于阈值且 `retry_count >= 3`
- **THEN** `retry_check` 节点 MUST 路由到 `human_review_wait`（不再走 `handle_failure`）
- **AND** `human_reviews.trigger_type` 为 `review_failed`
- **AND** `trigger_reason` 包含最后一次评分与失败摘要

#### Scenario: 工作流异常转人工兜底

- **WHEN** 工作流执行抛出未捕获异常
- **THEN** 系统 MUST 创建 `trigger_type=error_fallback` 的人工审核记录
- **AND** `trigger_reason` 包含异常摘要
- **AND** 工单状态从 `failed` 调整为 `pending_human_review`

#### Scenario: 用户主动申请复审

- **WHEN** 已 `completed` 的工单用户通过反馈接口提交 `satisfied=false`
- **THEN** 系统 MUST 创建 `trigger_type=user_request` 的人工审核记录
- **AND** 工单状态回到 `pending_human_review`

### Requirement: 工作流暂停节点实现

`human_review_wait` 节点 SHALL 在完成以下副作用后正常结束本次工作流执行（不使用 LangGraph interrupt）：

1. 调用 CoordinatorAgent 的 `suggest_decision` 生成 AI 辅助建议
2. 写入 `human_reviews` 行（status=`pending`）
3. 更新 `tickets.status = pending_human_review`
4. 通过 WebSocket 广播 `review_requested` 事件
5. 结束本次工作流执行（返回 END）

#### Scenario: 暂停节点写入审核记录

- **WHEN** 任意触发场景将工单送入 `human_review_wait`
- **THEN** 该节点 MUST 完成 5 项副作用后正常返回
- **AND** 不阻塞等待人工响应
- **AND** 后续可通过 API 触发独立的恢复执行

### Requirement: 工作流恢复节点路由

`apply_human_decision` 节点 SHALL 接收审核员决策并按以下矩阵决定后续路由：

| decision | 后继节点 | 处理结果来源 |
| --- | --- | --- |
| `approve` | `notify` | 沿用工单原 `processing_result` |
| `rewrite` | `notify` | 使用 `rewritten_result` 覆盖 `processing_result` |
| `reprocess` | `process` | 清空 `processing_result` 与 `retry_count`，重新处理 |
| `reject` | `complete` | 标记 `processing_result` 为已驳回 |

#### Scenario: 审核员通过工单

- **WHEN** 审核员提交 `decision=approve` 与 `decision_reason`
- **THEN** `apply_human_decision` 节点 MUST 路由到 `notify`
- **AND** `processing_result` 保持不变
- **AND** 后续走 `notify → complete`

#### Scenario: 审核员改写处理结果

- **WHEN** 审核员提交 `decision=rewrite` 与非空 `rewritten_result`
- **THEN** 工单 `processing_result` MUST 被覆盖为 `rewritten_result`
- **AND** 路由到 `notify`

#### Scenario: 审核员要求重新处理

- **WHEN** 审核员提交 `decision=reprocess`
- **THEN** `processing_result` MUST 被清空
- **AND** `retry_count` MUST 重置为 0
- **AND** 路由到 `process` 节点重新执行处理 Agent

#### Scenario: 审核员驳回工单

- **WHEN** 审核员提交 `decision=reject`
- **THEN** 工单 MUST 直接进入 `complete` 节点
- **AND** `processing_result` 标记为 rejected

### Requirement: 人工审核数据持久化

系统 SHALL 通过 `human_reviews` 表持久化审核全过程：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `review_id` | TEXT PK | 格式 `HR-<trace_id>` |
| `ticket_id` | TEXT NOT NULL | 关联工单 |
| `trigger_type` | TEXT NOT NULL | escalate / review_failed / error_fallback / user_request |
| `trigger_reason` | TEXT | 升级原因或失败摘要 |
| `ai_suggestion` | TEXT | CoordinatorAgent 生成的辅助建议 JSON |
| `decision` | TEXT | approve / reject / rewrite / reprocess |
| `decision_reason` | TEXT | 审核员填写的理由 |
| `rewritten_result` | TEXT | 改写后的处理结果（仅 rewrite） |
| `reviewer_id` | TEXT | 审核员标识 |
| `status` | TEXT NOT NULL | pending / decided |
| `created_at` | TIMESTAMP | 创建时间 |
| `decided_at` | TIMESTAMP | 决策时间 |

系统 SHALL 在以下字段上建立索引：`status`、`ticket_id`、`trigger_type`、`reviewer_id`。系统 SHALL 在 `tickets(status, created_at)` 上建立部分索引 `idx_tickets_pending` 仅包含 `status='pending_human_review'` 行以加速队列查询。

#### Scenario: 待审核记录创建

- **WHEN** 工单进入 `human_review_wait`
- **THEN** `human_reviews` MUST 插入一行 `status=pending` 的记录
- **AND** `ai_suggestion` 字段 MUST 包含 CoordinatorAgent 输出的 JSON
- **AND** `created_at` MUST 被填充

#### Scenario: 审核决策落库

- **WHEN** 审核员通过 API 提交决策
- **THEN** 对应 `human_reviews` 行 MUST 更新为 `status=decided`
- **AND** `decision`、`decision_reason`、`reviewer_id`、`decided_at` 字段 MUST 被填充

#### Scenario: 历史审核可追溯

- **WHEN** 同一工单多次进入人工审核（如 reprocess 后再次失败）
- **THEN** `human_reviews` 表 MUST 保留所有历史行
- **AND** 按 `created_at` 排序可还原完整审核时间线

### Requirement: CoordinatorAgent 辅助决策建议

CoordinatorAgent SHALL 提供 `suggest_decision` 方法，输入 `ticket_id`、`trigger_type`、`trigger_reason`、`processing_result`、`review_score`，输出包含以下字段的 JSON：

- `recommended_decision`：approve / reject / rewrite / reprocess
- `confidence`：0.0-1.0
- `reasoning`：建议理由文本
- `key_concerns`：审核员应重点关注的列表

LLM 不可用时 MUST 按规则降级：`escalate → reprocess`、`review_failed → rewrite`、其他 → `approve`，置信度统一为 0.3-0.6。

#### Scenario: LLM 可用时生成结构化建议

- **WHEN** CoordinatorAgent 调用 `suggest_decision` 且 LLM 服务正常
- **THEN** 返回的 JSON MUST 包含全部 4 个字段
- **AND** `recommended_decision` MUST 为合法枚举值
- **AND** `confidence` MUST 在 0.0-1.0 范围内

#### Scenario: LLM 不可用时降级

- **WHEN** LLM 调用抛出 `APIConnectionError` 或超时
- **THEN** CoordinatorAgent MUST 走 fallback 规则返回建议
- **AND** 不抛出异常给上层调用
- **AND** 建议的 `confidence` 反映降级来源（不超过 0.6）

### Requirement: 人工决策追踪

系统 SHALL 通过 Trace 系统记录人工决策全过程，新增 span 类型 `human_decision`。

`human_decision` span MUST 包含：

- `input_data.trigger_type`、`input_data.trigger_reason`、`input_data.ai_suggestion`
- `output_data.decision`、`output_data.decision_reason`、`output_data.reviewer_id`、`output_data.ai_adopted`
- `duration`：从挂起到决策的耗时（秒级）
- `metadata.review_id`

`ai_adopted` 字段 MUST 在决策与 AI 建议 `recommended_decision` 一致时为 `true`，否则为 `false`。

#### Scenario: 审核决策创建 span

- **WHEN** 审核员提交决策
- **THEN** 系统 MUST 在工单 trace 下创建 `span_type=human_decision` 的 span
- **AND** `ai_adopted` 字段根据决策与 AI 建议的一致性计算

#### Scenario: ai_adoption_rate 指标可计算

- **WHEN** 统计接口查询一段时间内的审核记录
- **THEN** 系统 MUST 能基于 `ai_adopted` 字段计算 AI 建议采纳率
- **AND** 该指标可用于论文评估人机协同效果
