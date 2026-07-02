## ADDED Requirements

### Requirement: 健康检查接口

系统 SHALL 提供 `GET /health` 接口返回服务整体状态与各组件状态。

响应结构 MUST 为：

```json
{
  "status": "ok" | "degraded",
  "components": {
    "qdrant": "ok" | "unavailable",
    "embedder": "ok" | "unavailable",
    "reranker": "ok" | "unavailable"
  }
}
```

任一关键组件不可用时 `status` MUST 为 `degraded`；全部正常时为 `ok`。HTTP 状态码始终为 200（不返回 5xx），由调用方根据 `status` 字段决定后续行为。

#### Scenario: 全部组件正常

- **WHEN** 调用 `/health` 且 Qdrant、Embedder、Reranker 均可用
- **THEN** 响应 MUST 为 `{status: "ok", components: {qdrant: "ok", embedder: "ok", reranker: "ok"}}`
- **AND** HTTP 状态码 MUST 为 200

#### Scenario: 单组件降级

- **WHEN** Reranker 模型加载失败，其他组件正常
- **THEN** 响应 MUST 为 `{status: "degraded", components: {qdrant: "ok", embedder: "ok", reranker: "unavailable"}}`
- **AND** HTTP 状态码 MUST 仍为 200

### Requirement: 组件健康检查方式

系统 SHALL 通过以下方式检查各组件：

- **Qdrant**：执行 `GET /collections`（或等价 ping 操作），返回 200 视为 ok
- **Embedder**：查询模型加载状态（首次加载完成后置为 ok）
- **Reranker**：查询模型加载状态（懒加载场景下首次调用前为 unavailable）

#### Scenario: Qdrant 连通性检查

- **WHEN** 调用 `/health`
- **THEN** 系统 MUST 实际发起 Qdrant ping 请求
- **AND** Qdrant 不可达时 `components.qdrant` MUST 为 `unavailable`

#### Scenario: Embedder 加载状态查询

- **WHEN** 模型尚未完成首次加载
- **THEN** `components.embedder` MUST 为 `unavailable`
- **WHEN** 模型加载完成
- **THEN** `components.embedder` MUST 为 `ok`

### Requirement: 降级决策由调用方决定

系统 SHALL 不在 `/health` 返回 `degraded` 时强制拒绝业务请求。调用方（主系统 RAG Client）根据自身策略决定是否走降级分支。

降级决策矩阵：

| health 状态 | 主系统行为（建议） |
| --- | --- |
| `status=ok` | 正常调用 `/retrieve` `/rerank` |
| `status=degraded`，reranker 不可用 | 仅调用 `/retrieve`，跳过 `/rerank` |
| `status=degraded`，embedder 不可用 | `/retrieve` mode 自动切换为 bm25 |
| `status=degraded`，qdrant 不可用 | 走无知识增强分支，不调用 rag-service |

#### Scenario: 主系统根据 health 决策降级

- **WHEN** 主系统 RAG Client 调用 `/health` 发现 `components.reranker=unavailable`
- **THEN** 主系统 MUST 跳过 `/rerank` 调用
- **AND** 仅使用 `/retrieve` 返回的结果作为上下文

### Requirement: 业务接口的降级返回

业务接口（`/retrieve` `/rerank` `/parse` `/ingest`）SHALL 在自身依赖的组件不可用时返回合适的 HTTP 状态码与业务码：

- Qdrant 不可用：`503 QDRANT_UNAVAILABLE`（`/retrieve` `/ingest` 受影响）
- Embedder 不可用且 mode=vector：自动降级为 bm25，200 + warning
- Reranker 不可用：按原 score 排序，200 + warning
- PDF 版面模型不可用：降级为 fixed 分块，200 + warning

降级原则：**宁可返回次优结果（带 warning），也不要直接 500，让主系统能继续工作。**

#### Scenario: Qdrant 不可用返回 503

- **WHEN** 调用 `/retrieve` 时 Qdrant 不可达
- **THEN** 系统 MUST 返回 `503 QDRANT_UNAVAILABLE`
- **AND** 不返回部分结果

#### Scenario: Embedder 不可用自动切换 mode

- **WHEN** 调用 `/retrieve mode=vector` 且 Embedder 不可用
- **THEN** 系统 MUST 自动切换为 bm25 模式
- **AND** 响应 MUST 包含 `warning: "embedder_unavailable_degraded_to_bm25"`
- **AND** HTTP 状态码 MUST 为 200

#### Scenario: PDF 版面模型不可用降级分块

- **WHEN** 调用 `/parse` 或 `/ingest` 上传 PDF 且版面分析模型加载失败
- **THEN** 系统 MUST 降级为 `fixed` 分块策略
- **AND** 响应 MUST 包含 `warning: "layout_model_unavailable_degraded_to_fixed"`
- **AND** layout_summary 字段 MUST 标注为降级产出
