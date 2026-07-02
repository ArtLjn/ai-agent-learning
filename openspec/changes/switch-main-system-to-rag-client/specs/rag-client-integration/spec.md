## ADDED Requirements

### Requirement: 主系统 RAG Client HTTP 调用

系统 SHALL 通过 `src/multi_agent_system/tools/rag_client.py` 中的 `RagClient` 类 HTTP 调用 rag-service，替换 v1.x 的内部 `KnowledgeSearchTool` 直连 Qdrant 模式。

`RagClient` MUST 提供以下方法：

- `async def retrieve(query, collection, mode="hybrid", top_k=10, filters=None, use_hyde=False) -> list[dict]`
- `async def rerank(query, documents, top_k=5, model=None) -> list[dict]`
- `async def retrieve_and_rerank(query, collection, top_k=20, final_top_k=5) -> list[dict]`（两阶段组合便捷方法）
- `async def health() -> dict`（返回 `{status, components}`）

#### Scenario: 正常调用 rag-service 检索

- **WHEN** ReActProcessorAgent 在 process 节点需要 RAG 上下文
- **THEN** 系统 MUST 通过 `RagClient.retrieve_and_rerank` 发起 HTTP 请求到 rag-service
- **AND** 调用 `POST /retrieve` 召回 top-20 候选
- **AND** 调用 `POST /rerank` 精排取 top-5
- **AND** 返回的候选片段列表 MUST 含 `content`、`score`、`doc_id`、`chunk_index`、`metadata`

#### Scenario: RagClient 单例注入

- **WHEN** FastAPI app 启动
- **THEN** 系统 MUST 创建 `RagClient` 单例并注入到 `app.state.rag_client`
- **AND** ReActProcessorAgent 实例化时通过构造函数参数 `rag_client` 获取该单例
- **AND** 不在每次请求时重复创建 `RagClient`（复用 httpx connection pool）

### Requirement: 超时与重试策略

`RagClient` SHALL 对每次 HTTP 调用实施以下超时与重试策略：

- 单次请求超时：10 秒（可配置 `rag_service.timeout`）
- 网络错误（ConnectionError、Timeout）重试 1 次，间隔 500ms
- 5xx 错误不重试，直接抛出
- 4xx 错误不重试，直接抛出

#### Scenario: 网络错误触发重试

- **WHEN** 首次调用 rag-service 抛出 `httpx.ConnectError`
- **THEN** 系统 MUST 等待 500ms 后重试 1 次
- **AND** 重试仍失败时累加 `_failure_count` 并抛 `RagServiceUnavailable`

#### Scenario: 5xx 错误不重试

- **WHEN** rag-service 返回 HTTP 503
- **THEN** 系统 MUST 不重试
- **AND** 直接抛出 `RagServiceUnavailable`

#### Scenario: 10 秒超时触发

- **WHEN** rag-service 响应时间超过 10 秒
- **THEN** 系统 MUST 中断请求
- **AND** 触发重试逻辑（按网络错误处理）

### Requirement: 旧工具保留与配置开关

系统 SHALL 保留 v1.x 的 `tools/knowledge_search.py` 与 `tools/knowledge_tool_adapter.py` 作为降级备份，通过配置项 `legacy_knowledge_tool_enabled`（默认 `false`）控制是否注册到 LangGraph 工具表。

#### Scenario: 默认使用 RagClient

- **WHEN** `legacy_knowledge_tool_enabled=false`（默认）
- **THEN** 系统 MUST 使用 `RagClient` 调用 rag-service
- **AND** `KnowledgeSearchTool` MUST 不被实例化与注册

#### Scenario: 切换回旧工具（降级备份）

- **WHEN** 管理员设置 `legacy_knowledge_tool_enabled=true`
- **THEN** 系统 MUST 实例化 `KnowledgeSearchTool` 并通过 `register_knowledge_tool` 注册到工具表
- **AND** ReActProcessorAgent 可回退到 v1.x 内部 Qdrant 直连模式
- **AND** 该模式用于答辩演示「v1.x vs v2.0」对照或 rag-service 项目迁移受阻时的应急方案

### Requirement: RAG 调用决策点埋点

系统 SHALL 为每次 RAG 调用写入独立的 `tool_call` span，`metadata` 中携带 `rag_stats` 子结构。

`rag_stats` 字段 MUST 包含：

- `hit_count`：检索返回的候选数量
- `top_score`：top-1 候选的 score
- `retrieval_mode`：`vector` / `bm25` / `hybrid`
- `rag_service_reachable`：`true` / `false`

#### Scenario: 正常 RAG 调用写入 rag_stats

- **WHEN** ReActProcessorAgent 成功调用 rag-service 检索到 5 条候选
- **THEN** 对应的 `tool_call` span MUST 写入 `metadata.rag_stats = {hit_count: 5, top_score: 0.91, retrieval_mode: "hybrid", rag_service_reachable: true}`
- **AND** span `status` MUST 为 `ok`

#### Scenario: Memory 加载独立 span

- **WHEN** ReActProcessorAgent 调用 `MemoryManager.load_memory()` 加载历史对话
- **THEN** 系统 MUST 创建独立的 `span_type=node`、`name=memory_load` 的 span
- **AND** span MUST 记录加载的 memory 条数与耗时
- **AND** 不与 RAG 调用混在同一个 span 中

### Requirement: classify span reason 字段

系统 SHALL 在 ClassifierAgent 的 classify node span 的 `output_data.reason` 字段写入分类理由文本。

#### Scenario: classify 决策结构化记录

- **WHEN** ClassifierAgent 输出 `category=technical, priority=P1`
- **THEN** 对应 span 的 `output_data` MUST 包含 `category`、`priority`、`reason` 三个字段
- **AND** `reason` MUST 为非空的自然语言分类理由（如「用户描述含 '崩溃' 关键词，归类为技术问题」）
