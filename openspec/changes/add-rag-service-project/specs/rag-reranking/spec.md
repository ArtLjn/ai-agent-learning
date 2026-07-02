## ADDED Requirements

### Requirement: Cross-Encoder 重排能力

系统 SHALL 提供 `/rerank` 接口，使用 Cross-Encoder 模型 `BAAI/bge-reranker-v2-m3` 对候选文档列表重排，按相关性降序输出 top_k 结果。

Cross-Encoder 工作方式 MUST 为：query 与每个 candidate 拼接后输入 Transformer，输出相关性 score，按 score 降序取 top_k。

#### Scenario: 对候选文档重排

- **WHEN** 调用方调用 `/rerank` 传入 query 与 20 个候选文档，`top_k=5`
- **THEN** 系统 MUST 用 Cross-Encoder 计算每个候选与 query 的相关性 score
- **AND** 按 score 降序排序后返回前 5 个文档
- **AND** 返回结果每项 MUST 包含 `content`、`score`、原始 `doc_id`/`chunk_index`

#### Scenario: 默认模型与自定义模型

- **WHEN** 调用方未指定 `model` 参数
- **THEN** 系统 MUST 使用默认 `BAAI/bge-reranker-v2-m3`
- **WHEN** 调用方指定 `model` 参数
- **THEN** 系统 MUST 尝试加载指定模型，加载失败时降级

### Requirement: 模型懒加载与启动检查

系统 SHALL 在服务启动时尝试加载 Cross-Encoder 模型；加载失败时记录 warning 日志但不阻塞启动，采用懒加载策略（首次 `/rerank` 调用时再次尝试加载）。

系统 SHALL 通过 `/health` 接口暴露 reranker 组件状态（`ok` / `unavailable`）。

#### Scenario: 模型预加载成功

- **WHEN** 服务启动且模型文件可访问
- **THEN** 系统 MUST 在启动时完成模型加载
- **AND** `/health` 接口的 `components.reranker` MUST 为 `ok`

#### Scenario: 模型预加载失败但启动不阻塞

- **WHEN** 服务启动时模型文件缺失或加载异常
- **THEN** 系统 MUST 记录 warning 日志并继续启动
- **AND** `/health` 接口的 `components.reranker` MUST 为 `unavailable`

### Requirement: 重排降级策略

系统 SHALL 在 Cross-Encoder 模型不可用时降级为按调用方传入的原始 score 排序，并在响应中携带 `warning` 字段标识降级。

降级触发条件：

- 模型首次加载失败且懒加载再次尝试仍失败
- 模型推理过程中抛出异常

#### Scenario: 模型不可用时按原 score 排序

- **WHEN** 调用 `/rerank` 且 Cross-Encoder 模型不可用
- **THEN** 系统 MUST 按调用方传入的原始 score 降序排序
- **AND** 响应 MUST 包含 `warning: "reranker_degraded"` 字段
- **AND** HTTP 状态码 MUST 为 200（不阻塞主系统流程）

#### Scenario: 候选文档无原 score 时的降级

- **WHEN** 调用 `/rerank` 且 Cross-Encoder 模型不可用，同时候选文档未携带原始 score
- **THEN** 系统 MUST 返回 `507 RERANKER_MODEL_UNAVAILABLE`
- **AND** 错误消息 MUST 提示调用方在文档中携带 `score` 字段以触发降级排序

### Requirement: 重排性能约束

系统在 Cross-Encoder 模型可用时，对 top-20 候选文档重排的延迟 SHALL 在 CPU 推理场景下不超过 1.5 秒（毕设 demo 规模）。

模型显存占用 SHALL 不超过 2GB，单卡 4G 或 CPU 推理可承载。

#### Scenario: CPU 推理延迟

- **WHEN** 在 CPU 模式下对 20 个候选文档重排
- **THEN** 系统 MUST 在 1.5 秒内完成
- **AND** 若超时 MUST 记录 warning 日志便于性能调优

### Requirement: 与 /retrieve 的协作

系统设计上 SHALL 支持 `/retrieve` 召回 top-20 → `/rerank` 精排取 top-5 的两阶段流程。调用方依次调用两个接口完成完整检索重排链路。

`/rerank` 接口的 `documents` 参数 MUST 接受 `/retrieve` 返回的结果格式（含 `content`、`score`、`doc_id`、`chunk_index`、`metadata` 字段）。

#### Scenario: 两阶段检索重排链路

- **WHEN** 调用方先调用 `/retrieve top_k=20` 召回候选
- **THEN** 调用方可将 `/retrieve` 的 `data.results` 作为 `/rerank` 的 `documents` 参数
- **AND** `/rerank` 返回的 top-5 结果 MUST 保留原始 metadata
- **AND** 主系统 RAG Client 据此拼接进 ReAct prompt 作为知识上下文
