## ADDED Requirements

### Requirement: RAG 调试透传接口

系统 SHALL 提供 `POST /api/admin/rag/debug` 接口，透传请求到 rag-service 的 `/retrieve` 与 `/rerank`，主系统不做任何检索/重排逻辑。

请求体 MUST 支持：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | string | 是 | 查询文本 |
| `mode` | enum | 是 | `vector` / `bm25` / `hybrid` |
| `top_k` | int | 否 | 默认 10 |
| `rerank` | bool | 否 | 默认 true，是否触发重排 |
| `collection` | string | 否 | 默认 `knowledge_base` |

响应 MUST 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `query` | string | 回显查询 |
| `mode` | enum | 回显模式 |
| `retrieval_results` | array | rag-service `/retrieve` 原始结果 |
| `rerank_results` | array | 重排后结果，每项含 `rank_change` 字段 |
| `elapsed_ms` | int | 总耗时 |

#### Scenario: hybrid 模式带重排

- **WHEN** 开发人员提交 `{query: "登录失败排查", mode: "hybrid", top_k: 10, rerank: true}`
- **THEN** 系统 MUST 先调用 rag-service `/retrieve?mode=hybrid`
- **AND** 再调用 `/rerank`（rerank=true 时）
- **AND** 响应 `rerank_results` 每项含 `rank_change`（原始 rank - 重排后 rank，正数表示上升）

#### Scenario: 关闭重排

- **WHEN** 请求 `rerank: false`
- **THEN** 系统 MUST 跳过 `/rerank` 调用
- **AND** 响应 `rerank_results` 为 null 或空数组

### Requirement: 三种检索模式透传

`mode` 字段的三个值 MUST 1:1 映射到 rag-service 端点：

| mode | rag-service 调用 |
| --- | --- |
| `vector` | `GET/POST /retrieve?mode=vector` |
| `bm25` | `GET/POST /retrieve?mode=bm25` |
| `hybrid` | `GET/POST /retrieve?mode=hybrid` |

主系统 MUST NOT 在本地实现检索逻辑，全部由 rag-service 承担。

#### Scenario: 纯向量检索

- **WHEN** mode=`vector`
- **THEN** 系统 MUST 透传到 rag-service 的 vector 模式
- **AND** 不触发 BM25 分支

### Requirement: 排名变化计算

系统 SHALL 在主系统侧计算 `rank_change` 字段，规则为：原始结果中 chunk_id 的 rank - 重排后该 chunk_id 的 rank。

- 正数表示排名上升（重排后更靠前）
- 负数表示排名下降
- 0 表示排名不变
- 仅出现在一侧（重排后被剔除或新进入）的 chunk，`rank_change` 为 null

#### Scenario: 排名上升

- **WHEN** chunk ch-01 原始 rank=3，重排后 rank=1
- **THEN** `rank_change` MUST 为 2

#### Scenario: 重排后被剔除

- **WHEN** chunk ch-99 原始 rank=10，重排后未出现在 top_k
- **THEN** `rerank_results` MUST 不包含 ch-99
- **AND** `retrieval_results` 中保留 ch-99（原始结果完整保留）

### Requirement: rag-service 不可用时降级

当 rag-service 调用失败（超时 / 连接错误 / 5xx）时，系统 MUST 返回结构化错误而非 500 异常。

- 超时默认 10s
- 响应 `{error: "rag_service_unavailable", detail: "...", elapsed_ms: ...}`，HTTP 状态 502
- 前端 RagDebugger MUST 在错误响应时显示行内错误提示，不崩溃

#### Scenario: rag-service 宕机

- **WHEN** rag-service 不可达
- **THEN** 系统 MUST 返回 502 与 `rag_service_unavailable` 错误
- **AND** 不抛未捕获异常

### Requirement: 前端 RAG 调试器布局

前端 `web/src/pages/dev/RagDebugger.tsx` SHALL 提供查询表单 + 结果对比表 + 片段详情 Sheet 三段布局。

- 查询表单：query 输入、mode 单选（vector/bm25/hybrid）、top_k 数字输入、rerank 开关、collection 下拉
- 结果对比表：列含 Rank / 片段ID / 原始分 / 重排分 / 变化（↑↓箭头）/ metadata
- 点击单行 MUST 展开ChunkDetailSheet，显示原文 / score / model / metadata

#### Scenario: 展开片段详情

- **WHEN** 开发人员点击结果表第 1 行
- **THEN** ChunkDetailSheet MUST 展开显示 chunk 原文（payload.text）
- **AND** 显示 score、rerank model（如 bge-reranker-v2-m3）、metadata（doc_id / page / chunk_idx / source）

#### Scenario: 排名变化可视化

- **WHEN** 重排前后排名变化
- **THEN** 「变化」列 MUST 显示 ↑ 或 ↓ 箭头 + 数字（如 ↑2）
- **AND** 排名上升绿色、下降灰色
