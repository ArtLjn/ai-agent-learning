## ADDED Requirements

### Requirement: 健康检查聚合接口

系统 SHALL 提供 `GET /api/admin/health` 接口，并发探测 4 个依赖服务的健康状态。

响应 MUST 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `overall` | enum | `healthy` / `degraded` / `unhealthy` |
| `services[]` | array | 4 个依赖的探测结果 |
| `checked_at` | datetime | 探测时间戳 |

每个 service 项 MUST 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | `rag_service` / `qdrant` / `llm` / `embedding` |
| `status` | enum | `healthy` / `unhealthy` |
| `latency_ms` | int | 探测耗时（unhealthy 时为 null） |
| `error` | string | unhealthy 时的错误描述（healthy 时为 null） |

#### Scenario: 全部健康

- **WHEN** 4 个依赖全部探测成功
- **THEN** `overall` MUST 为 `healthy`
- **AND** 每个 service `status` 为 `healthy`，含 `latency_ms`

#### Scenario: 部分不可用

- **WHEN** 仅 Qdrant 探测失败
- **THEN** `overall` MUST 为 `degraded`
- **AND** Qdrant 项 `status` 为 `unhealthy`，含 `error` 字段

#### Scenario: 全部不可用

- **WHEN** 4 个依赖全部探测失败
- **THEN** `overall` MUST 为 `unhealthy`

### Requirement: 探测超时降级不抛异常

每个依赖的探测 MUST 设置 2s 超时，超时或异常时返回 `status: "unhealthy"`，**不得抛出未捕获异常导致接口 500**。

- rag-service：GET `/healthz`，超时 2s
- Qdrant：通过 rag-service 透传（GET `/healthz?qdrant=1` 或 rag-service 暴露的子检查）
- LLM：HEAD `/v1/models` 或最小 chat completion（取决于 LLM 网关）
- Embedding：HEAD `/v1/embeddings`

#### Scenario: rag-service 超时

- **WHEN** rag-service `/healthz` 在 2s 内未响应
- **THEN** rag_service 项 MUST 为 `status: "unhealthy"`，`error: "timeout"`
- **AND** `latency_ms` 为 null

#### Scenario: LLM 网关返回 5xx

- **WHEN** LLM `/v1/models` 返回 503
- **THEN** llm 项 MUST 为 `status: "unhealthy"`
- **AND** `error` 字段含状态码与摘要

### Requirement: 4 个依赖并发探测

4 个依赖 MUST 并发探测（如 `asyncio.gather`），总响应时间不超过最慢单项 + 小开销（约 2.5s 上限）。

#### Scenario: 并发加速

- **WHEN** 4 个依赖各自耗时 1s
- **THEN** 接口总响应时间 MUST < 1.5s（而非 4s 串行）

### Requirement: 前端 ServiceHealth 卡片网格

前端 `web/src/pages/dev/ServiceHealth.tsx` SHALL 渲染 4 张卡片（rag-service / Qdrant / LLM / Embedding），状态色阶：

- 绿：healthy
- 黄：degraded（overall 状态，单卡不出现）
- 红：unhealthy

- 每张卡显示 `name` / `status` / `latency_ms`
- 自动每 30s 刷新（可通过 `setInterval` 实现）
- 提供手动刷新按钮
- unhealthy 卡片 MUST 显示 `error` 文本

#### Scenario: 自动刷新

- **WHEN** 开发人员停留在 ServiceHealth 页面
- **THEN** 页面 MUST 每 30s 自动调用 `GET /api/admin/health`
- **AND** 卡片状态实时更新

#### Scenario: 手动刷新

- **WHEN** 开发人员点击刷新按钮
- **THEN** 系统 MUST 立即调用接口
- **AND** 显示 loading 态直到响应返回

#### Scenario: 显示错误详情

- **WHEN** 某 service 为 unhealthy
- **THEN** 对应卡片 MUST 红色高亮
- **AND** 卡片下方显示 `error` 文本（如 "timeout" / "503 Service Unavailable"）
