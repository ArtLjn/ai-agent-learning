## ADDED Requirements

### Requirement: 待审核队列查询 API

系统 SHALL 提供 `GET /api/reviews/queue` 端点返回当前 `pending_human_review` 状态的工单列表。

支持的查询参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `trigger_type` | string | 无 | escalate / review_failed / error_fallback / user_request |
| `category` | string | 无 | 工单分类筛选 |
| `priority` | string | 无 | 工单优先级筛选 |
| `limit` | int | 20 | 上限 100 |
| `offset` | int | 0 | 分页偏移 |

响应 MUST 包含 `queue` 数组、`total`、`limit`、`offset` 字段。每个队列项 MUST 包含 `review_id`、`ticket_id`、`trigger_type`、`trigger_reason`、`content_preview`、`category`、`priority`、`ai_suggestion`、`waiting_seconds`、`created_at`。

队列项 SHALL 按 `priority` 优先级（P0 > P1 > P2 > P3）+ `waiting_seconds` 降序排列。

#### Scenario: 默认查询返回全部待审核工单

- **WHEN** 客户端调用 `GET /api/reviews/queue` 无参数
- **THEN** 系统 MUST 返回最多 20 条 `status=pending` 的审核记录
- **AND** 每条记录包含完整字段
- **AND** 按 priority + waiting_seconds 排序

#### Scenario: 按触发类型筛选

- **WHEN** 客户端调用 `GET /api/reviews/queue?trigger_type=escalate`
- **THEN** 返回的队列 MUST 只包含 `trigger_type=escalate` 的记录

#### Scenario: 分页生效

- **WHEN** 客户端调用 `GET /api/reviews/queue?limit=10&offset=20`
- **THEN** 系统 MUST 跳过前 20 条，返回第 21-30 条
- **AND** `total` 字段反映筛选条件下的总数

### Requirement: 审核详情查询 API

系统 SHALL 提供 `GET /api/reviews/{ticket_id}` 端点返回该工单的完整审核上下文，包含：

- 工单原文、分类、优先级、当前状态
- AI 处理结果（`processing_result`）
- 完整执行 trace 摘要（节点列表 + 关键 span）
- 当前 pending 审核记录的 `ai_suggestion`
- 历史审核记录列表（如多次进入人工审核）

#### Scenario: 查询待审核工单的完整上下文

- **WHEN** 客户端调用 `GET /api/reviews/TK-20260627-001`
- **AND** 该工单处于 `pending_human_review` 状态
- **THEN** 系统 MUST 返回完整审核上下文
- **AND** `ai_suggestion` 字段非空

#### Scenario: 查询不存在工单

- **WHEN** 客户端调用 `GET /api/reviews/TK-NOT-EXIST`
- **THEN** 系统 MUST 返回 404 状态码与 `TICKET_NOT_FOUND` 错误码

### Requirement: 审核决策提交 API

系统 SHALL 提供 `POST /api/reviews/{ticket_id}/decision` 端点接收审核员决策。

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `decision` | string | 是 | approve / reject / rewrite / reprocess |
| `decision_reason` | string | 是 | 审核员填写的理由 |
| `rewritten_result` | string | decision=rewrite 时必填 | 改写后的处理结果 |
| `reviewer_id` | string | 是 | 审核员标识 |

行为：

1. 校验工单 `status=pending_human_review`，否则返回 409 `TICKET_NOT_PENDING`
2. 校验 `decision_reason` 非空，否则返回 400 `DECISION_REASON_REQUIRED`
3. 若 `decision=rewrite` 校验 `rewritten_result` 非空，否则返回 400 `REWRITE_RESULT_REQUIRED`
4. 更新 `human_reviews` 行为 `decided`
5. 触发 `apply_human_decision` 节点恢复工作流
6. 通过 WebSocket 广播 `review_decided` 事件

响应 MUST 包含 `status`、`ticket_id`、`next_node`、`workflow_resumed` 字段。

#### Scenario: 成功提交通过决策

- **WHEN** 客户端对 `pending_human_review` 工单调用 `POST /api/reviews/TK-.../decision` 提交 `{decision: "approve", decision_reason: "结果合理", reviewer_id: "R001"}`
- **THEN** 系统 MUST 返回 200 与 `{status: "ok", next_node: "notify", workflow_resumed: true}`
- **AND** `human_reviews` 行更新为 `decided`
- **AND** 工单状态离开 `pending_human_review`

#### Scenario: 工单不在待审核状态

- **WHEN** 客户端对 `completed` 状态工单提交决策
- **THEN** 系统 MUST 返回 409 与 `TICKET_NOT_PENDING` 错误码

#### Scenario: rewrite 决策缺少改写结果

- **WHEN** 客户端提交 `{decision: "rewrite", decision_reason: "..."}` 但未提供 `rewritten_result`
- **THEN** 系统 MUST 返回 400 与 `REWRITE_RESULT_REQUIRED` 错误码

#### Scenario: 缺少决策理由

- **WHEN** 客户端提交 `{decision: "approve", decision_reason: ""}`
- **THEN** 系统 MUST 返回 400 与 `DECISION_REASON_REQUIRED` 错误码

#### Scenario: 重复提交决策幂等性

- **WHEN** 客户端对同一工单的同一 pending 记录连续两次提交决策
- **THEN** 第一次 MUST 成功
- **AND** 第二次 MUST 返回 409（因为工单已离开 `pending_human_review`）

### Requirement: 审核统计 API

系统 SHALL 提供 `GET /api/reviews/stats` 端点返回审核统计，包含：

- `pending_count`：当前待审核数量
- `decided_today`：当日已决策数量
- `decision_distribution`：`{approve, rewrite, reprocess, reject}` 分布
- `avg_decision_seconds`：平均决策耗时（秒）
- `ai_adoption_rate`：审核员最终决策与 AI 建议一致的比例（0.0-1.0）

#### Scenario: 查询默认统计

- **WHEN** 客户端调用 `GET /api/reviews/stats`
- **THEN** 响应 MUST 包含全部 5 个字段
- **AND** `ai_adoption_rate` 基于 `human_decision` span 的 `ai_adopted` 字段计算

### Requirement: WebSocket 实时事件

系统 SHALL 在以下时机通过 `/ws/monitor` 端点广播事件：

**`review_requested` 事件**：工单进入 `human_review_wait` 时立即广播，payload 含 `ticket_id`、`trigger_type`、`priority`、`timestamp`。

**`review_decided` 事件**：审核员提交决策后广播，payload 含 `ticket_id`、`decision`、`reviewer_id`、`next_node`、`timestamp`。

事件 MUST 复用现有 `/ws/monitor` 全局监控端点，不引入新的 WebSocket 连接。

#### Scenario: 工单挂起时推送通知

- **WHEN** 工单进入 `human_review_wait`
- **THEN** 所有连接 `/ws/monitor` 的客户端 MUST 收到 `review_requested` 事件
- **AND** 事件 `type` 字段为 `review_requested`

#### Scenario: 决策提交后推送通知

- **WHEN** 审核员通过 API 成功提交决策
- **THEN** 所有 `/ws/monitor` 客户端 MUST 收到 `review_decided` 事件
- **AND** 事件包含 `next_node` 字段

### Requirement: 审核员工作台前端页面

系统 SHALL 提供前端页面 `ReviewWorkbench.tsx`，路径 `/reviews`，采用双栏布局：

**左栏（30%）待审核队列**：

- 支持按 `trigger_type`、`category`、`priority` 筛选
- 按 `priority` + `waiting_seconds` 排序
- 每条显示工单摘要、trigger 徽章、优先级色块、AI 建议摘要、等待时长
- 等待时长超过 30 分钟显示"超时"标记

**右栏（70%）审核详情面板**：

- 顶部：工单原文 + 分类徽章 + 优先级
- 中部：AI 处理结果展示
- 中下：执行 trace 时间线（节点列表 + 关键 span）
- 关键：AI 辅助决策建议卡（置信度 > 0.7 高亮）
- 底部：审核员决策区，含 4 个按钮（通过 / 改写 / 重处理 / 驳回）+ 改写文本框（仅 rewrite 时展开）+ 必填理由输入框

视觉规范：

- 优先级色块：P0 红 / P1 橙 / P2 黄 / P3 灰
- 决策按钮色：通过=绿 / 改写=蓝 / 重处理=黄 / 驳回=红
- 决策按钮触控目标 ≥ 44×44px

#### Scenario: 审核员打开工作台查看队列

- **WHEN** 审核员访问 `/reviews`
- **THEN** 左栏 MUST 显示当前所有待审核工单
- **AND** 队列按优先级排序
- **AND** 默认选中第一条展示在右栏

#### Scenario: 审核员点击队列项切换详情

- **WHEN** 审核员点击左栏任一队列项
- **THEN** 右栏 MUST 加载该工单的完整审核上下文
- **AND** 调用 `GET /api/reviews/{ticket_id}`

#### Scenario: 审核员通过工单

- **WHEN** 审核员点击"通过"按钮并填写理由
- **THEN** 前端 MUST 调用 `POST /api/reviews/{ticket_id}/decision` 提交 `{decision: "approve", ...}`
- **AND** 成功后该工单从队列中移除

#### Scenario: 审核员选择改写

- **WHEN** 审核员点击"改写"按钮
- **THEN** 决策区 MUST 展开 `rewritten_result` 文本框
- **AND** 提交时校验非空

#### Scenario: WebSocket 实时刷新队列

- **WHEN** 工作台收到 `review_requested` 事件
- **THEN** 队列顶部 MUST 出现新条目
- **AND** 显示 toast 提示

#### Scenario: 决策已被其他审核员提交

- **WHEN** 工作台收到 `review_decided` 事件且为当前选中工单
- **THEN** 右栏 MUST 刷新为已决策状态
- **AND** 禁用决策按钮
