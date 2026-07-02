## ADDED Requirements

### Requirement: RAG 服务不可用异常类

系统 SHALL 在 `tools/rag_client.py` 定义 `RagServiceUnavailable` 异常类，作为 rag-service 不可达时的统一异常信号。

`RagServiceUnavailable` MUST 在以下场景被抛出：

- 10 秒超时且重试 1 次后仍失败
- rag-service 返回 5xx 错误
- rag-service 连续 3 次调用失败后进入冷却期，冷却期内任何调用直接抛出

#### Scenario: 超时触发异常

- **WHEN** rag-service 响应超过 10 秒且重试仍超时
- **THEN** `RagClient` MUST 抛出 `RagServiceUnavailable`
- **AND** 异常 message MUST 包含 `ticket_id`（如可获取）、`endpoint`、`elapsed_time`

#### Scenario: 5xx 错误触发异常

- **WHEN** rag-service 返回 HTTP 503（QDRANT_UNAVAILABLE）
- **THEN** `RagClient` MUST 抛出 `RagServiceUnavailable`
- **AND** 异常 message MUST 包含状态码与业务错误码

### Requirement: 连续失败冷却机制

`RagClient` SHALL 维护 `_failure_count` 与 `_unavailable_until` 状态实现冷却机制：

- 每次调用失败（超时、5xx、网络错误）累加 `_failure_count`
- `_failure_count >= 3` 时设置 `_unavailable_until = now + cooldown_seconds`（默认 300 秒）
- 冷却期内任何调用直接抛 `RagServiceUnavailable`，不发起 HTTP 请求
- 冷却期过后首次调用若成功，重置 `_failure_count=0`；若失败则重新进入冷却

#### Scenario: 连续 3 次失败进入冷却

- **WHEN** rag-service 连续 3 次调用失败
- **THEN** `RagClient` MUST 设置 `_unavailable_until = now + 300s`
- **AND** 后续 5 分钟内的调用直接抛 `RagServiceUnavailable`
- **AND** 不实际发起 HTTP 请求（节省超时等待时间）

#### Scenario: 冷却期过后恢复

- **WHEN** 冷却期结束且下一次调用成功
- **THEN** `_failure_count` MUST 重置为 0
- **AND** `_unavailable_until` MUST 清空
- **AND** 后续调用恢复正常路径

#### Scenario: 冷却期过后仍失败

- **WHEN** 冷却期结束但下一次调用仍失败
- **THEN** `_failure_count` MUST 累加到 4
- **AND** 立即重新进入冷却期（`_unavailable_until` 更新）

### Requirement: 无知识增强降级分支

ReActProcessorAgent SHALL 在捕获 `RagServiceUnavailable` 异常后走「无知识增强」分支，保证工单主流程不中断。

降级分支行为 MUST 满足：

| 字段 | 正常路径 | 降级路径 |
| --- | --- | --- |
| `references_json` | top-5 重排片段 | 空数组 `[]` |
| `processing_result` | 结合知识库的解决方案 | 仅基于工单内容生成的解决方案 |
| `metadata.rag_stats` | `{hit_count, top_score, retrieval_mode}` | `{hit_count: 0, rag_service_reachable: false, retrieval_mode: "degraded"}` |
| 后续流程 | `review → notify/complete` | 完全一致（流程不中断） |

#### Scenario: 降级分支生成无知识方案

- **WHEN** ReActProcessorAgent 的 `_prefetch_knowledge` 捕获 `RagServiceUnavailable`
- **THEN** 系统 MUST 返回空 knowledge_context
- **AND** ReAct 主循环 MUST 仅基于工单内容继续生成 processing_result
- **AND** 工单 `references_json` MUST 设置为 `[]`
- **AND** 工单主流程 MUST 不中断（仍走 review → notify/complete）

#### Scenario: 降级时 rag_stats 标记

- **WHEN** 降级分支触发
- **THEN** 工单 `metadata.rag_stats` MUST 写入 `{hit_count: 0, rag_service_reachable: false, retrieval_mode: "degraded"}`
- **AND** 对应的 `tool_call` span 的 `status` MUST 为 `degraded`
- **AND** span `metadata` MUST 包含降级原因（如 `fallback_reason: "timeout"`）

#### Scenario: 降级不影响 review 流程

- **WHEN** process 节点走降级分支生成 processing_result
- **THEN** ReviewerAgent MUST 仍能对该 result 评分
- **AND** 评分低于阈值时仍触发 retry_check
- **AND** 完整的 retry → human_review_wait → 人工决策链路 MUST 不受影响

### Requirement: 降级 warning 日志与监控

系统 SHALL 在降级触发时记录 warning 级别日志，便于运维定位。

日志 MUST 包含：`ticket_id`、`trace_id`、`fallback_reason`（timeout / 5xx / cooldown）、`failure_count`、`unavailable_until`。

#### Scenario: 降级触发日志记录

- **WHEN** ReActProcessorAgent 捕获 `RagServiceUnavailable`
- **THEN** 系统 MUST 记录 warning 日志：`rag_service_unavailable ticket_id=TK-xxx fallback_reason=timeout failure_count=3 unavailable_until=2026-07-01T12:05:00`
- **AND** 该日志可在后续运维查询中按 `fallback_reason` 过滤

### Requirement: 健康检查驱动的降级

系统 SHALL 在 ReActProcessorAgent 首次调用 RAG 前可选地检查 rag-service `/health`，若 `status=degraded` 且 `components.reranker=unavailable`，则跳过 `/rerank` 调用，仅使用 `/retrieve` 结果。

#### Scenario: reranker 不可用跳过重排

- **WHEN** rag-service `/health` 返回 `{status: "degraded", components: {reranker: "unavailable"}}`
- **THEN** `RagClient` MUST 跳过 `/rerank` 调用
- **AND** 仅返回 `/retrieve` 的 top_k 结果作为上下文
- **AND** 不视为完整降级（references 非空）

#### Scenario: qdrant 不可用走完整降级

- **WHEN** rag-service `/health` 返回 `{status: "degraded", components: {qdrant: "unavailable"}}`
- **THEN** `RagClient` MUST 不发起 `/retrieve` 调用
- **AND** 直接抛 `RagServiceUnavailable`
- **AND** ReActProcessorAgent 走无知识增强分支
