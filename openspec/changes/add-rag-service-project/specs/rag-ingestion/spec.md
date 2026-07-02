## ADDED Requirements

### Requirement: 文档入库全链路

系统 SHALL 提供 `/ingest` 接口完成文档入库全链路：解析 → 分块 → 元数据清洗 → 向量化 → 写入 Qdrant + SQLite 元数据。

调用方 MUST 提供 `file`（multipart 上传）或 `text`（JSON 字段）二选一，以及目标 `collection` 名称。

#### Scenario: PDF 文档入库

- **WHEN** 调用方通过 `/ingest` 上传 PDF 文件并指定 collection=`ticket_knowledge`
- **THEN** 系统 MUST 完成解析 → 分块 → 向量化 → 写入 Qdrant 全流程
- **AND** SQLite `documents` 表 MUST 新增一行记录 doc_id、source、chunk_count、content_hash、ingested_at
- **AND** 响应 MUST 返回 `{doc_id, chunk_count, collection}`

#### Scenario: 文本直接入库

- **WHEN** 调用方通过 `/ingest` 传入 JSON `text` 字段
- **THEN** 系统 MUST 跳过文件解析，直接走分块 → 向量化 → 写入
- **AND** 响应格式与文件上传一致

#### Scenario: 缺少必需参数

- **WHEN** 调用 `/ingest` 未提供 `collection`
- **THEN** 系统 MUST 返回 `400` 错误
- **AND** 错误消息 MUST 提示 `collection` 为必填

### Requirement: 集合存在性校验

系统 SHALL 在入库前校验目标 collection 是否存在。collection 不存在时返回 `404 COLLECTION_NOT_FOUND`，不自动创建（避免向量维度配置错误）。

#### Scenario: collection 不存在

- **WHEN** 调用 `/ingest collection=non_existent`
- **THEN** 系统 MUST 返回 `404 COLLECTION_NOT_FOUND`
- **AND** 错误消息 MUST 提示调用方先调用 `POST /collections` 创建

### Requirement: 增量更新（content_hash 判等）

系统 SHALL 通过文档内容的 MD5 hash（content_hash）实现增量更新：

- 同一 `doc_id` 重新 ingest 时，计算新内容的 content_hash
- 与 SQLite `documents.content_hash` 对比，未变化则跳过写入并返回提示
- 变化则删除该 doc_id 在 Qdrant 中的所有旧 chunk，重新写入新 chunk，更新 content_hash

#### Scenario: 未变化的文档跳过入库

- **WHEN** 调用 `/ingest` 上传与已存在 doc_id 内容完全相同的文档
- **THEN** 系统 MUST 计算 content_hash 并发现与已存记录一致
- **AND** 跳过向量化与 Qdrant 写入
- **AND** 响应 MUST 返回 `chunk_count=0` 与 `message: "document unchanged"`

#### Scenario: 变化的文档替换旧 chunk

- **WHEN** 调用 `/ingest` 上传与已存在 doc_id 内容不同的文档
- **THEN** 系统 MUST 先删除 Qdrant 中该 doc_id 的所有旧 chunk
- **AND** 重新分块向量化写入新 chunk
- **AND** SQLite `documents.content_hash` MUST 更新为新 hash
- **AND** 响应 MUST 返回新的 `chunk_count`

### Requirement: 版本记录

系统 SHALL 在每次 ingest 时写入一条 `document_versions` 记录，包含 `version_id`、`doc_id`、`content_hash`、`ingested_at`、`chunk_count`。

毕设范围内仅记录版本，不做可视化回滚 UI。

#### Scenario: 每次入库写版本

- **WHEN** 调用 `/ingest` 成功写入文档
- **THEN** SQLite `document_versions` 表 MUST 新增一行
- **AND** 该行 MUST 关联到正确的 `doc_id` 与 `content_hash`

#### Scenario: 同 doc_id 多次入库产生多版本

- **WHEN** 同一 doc_id 被多次 ingest（内容变化）
- **THEN** `document_versions` 表 MUST 保留所有历史版本
- **AND** 按 `ingested_at` 排序可还原文档演进历史

### Requirement: Qdrant 不可用降级

系统 SHALL 在 Qdrant 不可用时返回 `503 QDRANT_UNAVAILABLE`，不将部分写入结果返回给调用方。

#### Scenario: Qdrant 连接失败

- **WHEN** 调用 `/ingest` 时 Qdrant 服务不可达
- **THEN** 系统 MUST 返回 `503 QDRANT_UNAVAILABLE`
- **AND** SQLite 元数据 MUST 不写入（保证 Qdrant 与元数据一致性）
- **AND** 主系统 RAG Client 收到 503 后走无知识增强分支

### Requirement: 文档元数据存储

系统 SHALL 在 SQLite `documents` 表为每个文档持久化以下元数据字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `doc_id` | TEXT PK | 文档内容指纹（MD5 前 8 位） |
| `collection` | TEXT | 所属 collection |
| `source` | TEXT | 调用方传入的来源标识 |
| `category` | TEXT | 业务类别（如 technical/policy） |
| `chunk_count` | INT | 分块数 |
| `content_hash` | TEXT | 内容 MD5 hash |
| `ingested_at` | TIMESTAMP | 入库时间 |

#### Scenario: 元数据完整持久化

- **WHEN** 调用 `/ingest` 成功
- **THEN** SQLite `documents` 行 MUST 包含所有 7 个字段
- **AND** `doc_id` 格式 MUST 为 MD5 前 8 位
- **AND** `ingested_at` MUST 反映实际入库时间
