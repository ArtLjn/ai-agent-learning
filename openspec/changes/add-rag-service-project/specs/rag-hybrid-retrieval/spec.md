## ADDED Requirements

### Requirement: 三种检索模式

系统 SHALL 在 `/retrieve` 接口支持三种检索模式，由请求参数 `mode` 控制：

- `vector`：Dense 向量检索（Qdrant dense 向量索引，余弦距离）
- `bm25`：Sparse BM25 检索（Qdrant sparse 向量索引，jieba 预分词）
- `hybrid`（默认）：RRF 融合 vector + bm25 结果

未指定 `mode` 时 MUST 默认 `hybrid`。`mode` 取值不在三选一时返回 `400 INVALID_MODE`。

#### Scenario: 默认混合检索

- **WHEN** 调用 `/retrieve` 未指定 `mode` 参数
- **THEN** 系统 MUST 使用 `hybrid` 模式
- **AND** 同时执行 vector 与 bm25 检索，RRF 融合后返回 top_k 结果

#### Scenario: 仅向量检索

- **WHEN** 调用 `/retrieve` 指定 `mode=vector`
- **THEN** 系统 MUST 仅执行 Dense 向量检索
- **AND** 不触发 BM25 检索

#### Scenario: 仅 BM25 检索

- **WHEN** 调用 `/retrieve` 指定 `mode=bm25`
- **THEN** 系统 MUST 仅执行 Sparse BM25 检索
- **AND** 中文查询 MUST 经过 jieba 分词

#### Scenario: 无效 mode 参数

- **WHEN** 调用 `/retrieve` 指定 `mode=unknown`
- **THEN** 系统 MUST 返回 `400 INVALID_MODE`
- **AND** 错误消息 MUST 列出合法的 mode 取值

### Requirement: 向量检索（Dense）

系统 SHALL 使用 Qdrant dense 向量索引提供向量检索能力：

- Embedding 模型：默认 `BAAI/bge-large-zh-v1.5`，可通过 `EMBEDDING_MODEL` 环境变量切换
- 距离度量：余弦距离
- `score_threshold` 默认 0.3，低于阈值的候选 MUST 被过滤
- 默认 `top_k=10`，可由调用方覆盖

#### Scenario: 向量检索召回相似文档

- **WHEN** 查询为"如何重置密码"且文档库中含密码重置说明
- **THEN** 系统 MUST 通过 Embedding 相似度召回相关 chunk
- **AND** 召回结果 MUST 按 score 降序排列

#### Scenario: score 阈值过滤

- **WHEN** 候选 chunk 的 score 低于 0.3
- **THEN** 系统 MUST 过滤掉这些低相关性候选
- **AND** 仅返回 score ≥ 阈值的结果

### Requirement: BM25 关键词检索（Sparse）

系统 SHALL 使用 Qdrant sparse 向量索引（内置 BM25 score）提供关键词检索能力：

- 中文查询 MUST 经过 jieba 分词预处理
- 适用于专有名词、产品型号、错误码等需要精确字面匹配的查询
- BM25 参数 `k1=1.5`、`b=0.75` 为常用初值

#### Scenario: 错误码精确匹配

- **WHEN** 查询为"错误码 ERR-5001"
- **THEN** 系统 MUST 通过 BM25 精确匹配文档中的 `ERR-5001` 字符串
- **AND** 召回结果 MUST 包含该错误码的 chunk 排在前列

#### Scenario: 中文分词

- **WHEN** 查询为中文长句
- **THEN** 系统 MUST 先用 jieba 分词将查询拆分为词项
- **AND** BM25 计算 MUST 基于分词结果

### Requirement: 混合检索（RRF 融合）

系统 SHALL 使用 Reciprocal Rank Fusion（RRF）算法融合 vector 与 bm25 检索结果：

- 公式：`score = Σ w_i / (k + rank_i)`
- 默认参数：`k=60`、向量权重 `w_vector=0.7`、sparse 权重 `w_sparse=0.3`
- 参数可通过环境变量 `RRF_K`、`RRF_VECTOR_WEIGHT`、`RRF_SPARSE_WEIGHT` 覆盖
- 长 query（>15 字）建议调高 `RRF_VECTOR_WEIGHT` 到 0.8；短 query 或带错误码建议调高 `RRF_SPARSE_WEIGHT` 到 0.5（由调用方按工单类别决定）

#### Scenario: 默认 RRF 融合

- **WHEN** 调用 `/retrieve mode=hybrid`
- **THEN** 系统 MUST 并发执行 vector 与 bm25 检索
- **AND** 使用 RRF 公式融合两路结果，权重为 vector 0.7 + bm25 0.3
- **AND** 返回融合后按 score 降序的 top_k 结果

#### Scenario: 长查询调高向量权重

- **WHEN** 查询为长自然语言描述（>15 字）且调用方设置 `RRF_VECTOR_WEIGHT=0.8`
- **THEN** 系统 MUST 按新权重融合
- **AND** 向量召回的候选 MUST 获得更高融合 score

### Requirement: HyDE 查询改写（可选）

系统 SHALL 提供可选的 HyDE（Hypothetical Document Embeddings）查询改写能力，由请求参数 `use_hyde` 控制，默认 `false`。

启用时系统 MUST：

1. 调用一次 LLM 生成「假设答案文档」
2. 使用假设答案的 Embedding 检索，而非原始 query 的 Embedding

#### Scenario: HyDE 默认关闭

- **WHEN** 调用 `/retrieve` 未指定 `use_hyde`
- **THEN** 系统 MUST 不启用 HyDE
- **AND** 直接使用原始 query 的 Embedding 检索

#### Scenario: 启用 HyDE 改写

- **WHEN** 调用 `/retrieve use_hyde=true` 且 query 过短或抽象
- **THEN** 系统 MUST 调用 LLM 生成假设答案文档
- **AND** 使用假设答案的 Embedding 检索
- **AND** 总延迟 MUST 包含 1 次额外 LLM 调用时间

### Requirement: 元数据过滤

系统 SHALL 支持通过请求参数 `filters` 对检索结果做元数据过滤。`filters` 为 JSON object，键为元数据字段名，值为期望的取值。

支持的过滤字段 MUST 至少包括：`category`、`source`、`heading_path`。

#### Scenario: 按 category 过滤

- **WHEN** 调用 `/retrieve filters={"category": "technical"}`
- **THEN** 系统 MUST 仅返回 `metadata.category=technical` 的 chunk
- **AND** 其他 category 的候选 MUST 被过滤

#### Scenario: 多字段组合过滤

- **WHEN** 调用 `/retrieve filters={"category": "policy", "source": "manual_v2"}`
- **THEN** 系统 MUST 同时按两个字段过滤
- **AND** 仅返回同时满足两个条件的 chunk
