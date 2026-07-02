## ADDED Requirements

### Requirement: 查询单工单 trace
系统 SHALL 提供 `GET /api/tickets/{ticket_id}/trace` 端点，返回该工单的完整执行链路。

响应格式：
```json
{
  "trace_id": "...",
  "ticket_id": "...",
  "status": "completed",
  "duration": 3.256,
  "total_tokens": 1250,
  "total_tool_calls": 2,
  "node_count": 5,
  "spans": [
    {
      "span_id": "...",
      "parent_span_id": null,
      "span_type": "node",
      "name": "receive",
      "status": "ok",
      "duration": 0.003,
      "input_data": {},
      "output_data": {},
      "metadata": {},
      "children": [...]
    }
  ]
}
```

spans MUST 组织为嵌套树结构（利用 parent_span_id 构建树）。

#### Scenario: 查询已有 trace
- **WHEN** GET /api/tickets/{ticket_id}/trace 请求且工单有 trace 记录
- **THEN** 返回 200，body 为 trace 及其 span 树

#### Scenario: 查询不存在的 trace
- **WHEN** GET /api/tickets/{ticket_id}/trace 请求但工单无 trace 记录
- **THEN** 返回 404，body 包含 {"detail": "Trace not found"}

### Requirement: trace 列表查询
系统 SHALL 提供 `GET /api/traces` 端点，支持分页和过滤。

查询参数：
- `status` (可选) — 过滤 trace 状态（running/completed/failed）
- `limit` (默认 20，最大 100) — 分页大小
- `offset` (默认 0) — 分页偏移

响应：按 start_time DESC 排序的 trace 列表（不含 span 详情）。

#### Scenario: 列表查询
- **WHEN** GET /api/traces?status=completed&limit=10
- **THEN** 返回最近 10 条已完成的 trace 摘要列表

#### Scenario: 默认分页
- **WHEN** GET /api/traces 无参数
- **THEN** 返回最近 20 条 trace

### Requirement: span 耗时统计
系统 SHALL 提供 `GET /api/traces/{trace_id}/stats` 端点，返回指定 trace 的耗时分析。

响应格式：
```json
{
  "trace_id": "...",
  "total_duration": 3.256,
  "by_type": {
    "node": {"count": 5, "avg_duration": 0.45, "max_duration": 2.5},
    "llm_call": {"count": 4, "avg_duration": 0.35, "max_duration": 0.7},
    "tool_call": {"count": 2, "avg_duration": 0.15, "max_duration": 0.18}
  },
  "slowest_spans": [
    {"name": "process", "span_type": "node", "duration": 2.5}
  ]
}
```

#### Scenario: 耗时分析
- **WHEN** GET /api/traces/{trace_id}/stats
- **THEN** 返回按 span_type 聚合的耗时统计和最慢的 5 个 span

### Requirement: WebSocket span 实时推送
系统 SHALL 在现有 `/ws/tickets/{ticket_id}` WebSocket 连接上推送 span 完成事件。

事件格式：
```json
{
  "type": "span_complete",
  "span_id": "...",
  "span_type": "node",
  "name": "classify",
  "duration": 0.115,
  "status": "ok"
}
```

#### Scenario: span 实时推送
- **WHEN** 一个 span 执行完成
- **THEN** 向订阅该 ticket_id 的 WebSocket 客户端推送 span_complete 事件

#### Scenario: 无 WebSocket 客户端
- **WHEN** span 完成但无 WebSocket 客户端订阅
- **THEN** 静默忽略，不影响 trace 记录
