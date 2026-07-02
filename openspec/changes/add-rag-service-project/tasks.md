## 1. 项目骨架

- [x] 1.1 创建目录 `/Users/ljn/Documents/demo/finished/rag-service/`，初始化 `pyproject.toml`（Python 3.11+，依赖：fastapi、uvicorn、qdrant-client、sentence-transformers、PyMuPDF、pytesseract、rank-bm25、jieba、httpx、pydantic）
- [x] 1.2 编写 `rag-service/Dockerfile`：基于 `python:3.11-slim`，安装 `tesseract-ocr` + 中文语言包 + 系统依赖，预下载 BAAI/bge-large-zh-v1.5 与 BAAI/bge-reranker-v2-m3 模型
- [x] 1.3 编写 `rag-service/docker-compose.yml`：编排 `rag-service`（端口 8001）+ `qdrant`（端口 6333）双服务，qdrant 数据卷持久化
- [x] 1.4 编写 `rag-service/.env.example`：PORT、QDRANT_URL、EMBEDDING_MODEL、RERANKER_MODEL、DEFAULT_CHUNK_SIZE、DEFAULT_TOP_K、RRF_K、RRF_VECTOR_WEIGHT、RRF_SPARSE_WEIGHT、HTTP_TIMEOUT 共 10 项配置
- [x] 1.5 创建 `rag-service/app/core/config.py`：使用 pydantic-settings 加载环境变量，集中暴露 `Settings` 实例
- [x] 1.6 创建 `rag-service/app/core/logging.py`：复用 loguru 配置，统一日志格式（与主系统一致）
- [x] 1.7 创建 `rag-service/app/core/exceptions.py`：定义 `RagServiceError` 基类与 `ParseFailed`/`QdrantUnavailable`/`ModelUnavailable` 子类
- [x] 1.8 创建 `rag-service/app/core/metrics.py`：暴露 Prometheus 风格计数器（解析次数、检索延迟、重排延迟）
- [x] 1.9 创建 `rag-service/app/main.py`：FastAPI 入口，注册 6 个路由模块，启动时初始化 Qdrant 连接与模型加载
- [x] 1.10 创建 `rag-service/app/models/` 下 `chunk.py`/`query.py`/`document.py` Pydantic 数据模型

## 2. PDF 复杂解析（NSQA 迁移）

- [x] 2.1 创建 `rag-service/app/parser/base.py`：定义 `BaseParser` 抽象基类，方法签名 `async def parse(self, content: bytes | str, metadata: dict) -> list[Chunk]`
- [x] 2.2 创建 `rag-service/app/parser/layout/`：从 NSQA 迁移版面分析网络，输入 PDF 每页，输出元素 bbox 与类别（title/paragraph/table/figure/formula/header/footer/list_item）
- [x] 2.3 在 layout 模块实现页眉页脚剔除（位置阈值 5% + 文本重复模式）、多栏布局还原（按列还原阅读顺序）、标题层级推断（字号/缩进/编号）
- [x] 2.4 创建 `rag-service/app/parser/table/`：从 NSQA 迁移表格识别，输出 HTML/Markdown 表格 + 单元格 OCR 文本回填 + 跨页表格拼接
- [x] 2.5 创建 `rag-service/app/parser/pdf_parser.py`：组合 layout + table + 文本结构化（段落合并、阅读顺序还原、公式 LaTeX OCR、图片 caption 提取）
- [x] 2.6 创建 `rag-service/app/parser/markdown_parser.py`：解析 MD 标题层级（ATX #）+ 代码块保留 + 列表结构化
- [x] 2.7 创建 `rag-service/app/parser/text_parser.py`：纯 TXT 解析，按段落分割
- [x] 2.8 在 `parser/base.py` 实现解析器工厂 `get_parser(file_type: str) -> BaseParser`

## 3. 智能分块与元数据清洗

- [x] 3.1 创建 `rag-service/app/parser/chunker/structure_aware.py`：按标题层级切分，同一二级标题下段落聚为一块，最大 800 字
- [x] 3.2 创建 `rag-service/app/parser/chunker/semantic.py`：基于句子 Embedding 相似度做语义边界检测（从 NSQA 迁移）
- [x] 3.3 创建 `rag-service/app/parser/chunker/fixed.py`：固定窗口 + 重叠（默认 500 字 + 50 重叠），复用主系统 `personal_knowledge_base/document_processor.py` 算法
- [x] 3.4 在 `chunker/__init__.py` 实现策略选择器：PDF → structure_aware，MD → semantic，TXT → fixed；调用方可显式覆盖
- [x] 3.5 创建 `rag-service/app/parser/cleaner.py`：去除断行符、统一空白字符、剔除 OCR 噪声字符（`□`、孤立单字符行）、长度低于阈值的 chunk 合并到相邻
- [x] 3.6 每个 chunk 输出统一元数据 schema：`{source, page, category, heading_path, doc_id, chunk_index}`

## 4. Qdrant 接入

- [x] 4.1 创建 `rag-service/app/storage/qdrant_client.py`：封装 QdrantClient 连接，从 `Settings.QDRANT_URL` 初始化，提供 `get_client()` 单例
- [x] 4.2 创建 `rag-service/app/storage/collection_manager.py`：实现 `create_collection(name, vector_dim, distance="Cosine")`、`delete_collection(name)`、`list_collections()`、`collection_exists(name)`
- [x] 4.3 collection 创建时同时配置 sparse 向量索引（Qdrant 原生 BM25 score），字段名 `text-sparse`
- [x] 4.4 创建 `rag-service/app/storage/metadata_store.py`：SQLite 元数据存储，表 `documents`（doc_id, source, category, chunk_count, content_hash, ingested_at）与 `document_versions`
- [x] 4.5 创建 `rag-service/app/storage/version_manager.py`：每次 ingest 写入一条 `document_version` 记录，支持查询历史版本（毕设不做可视化回滚 UI）

## 5. 入库 API（POST /ingest）

- [x] 5.1 创建 `rag-service/app/api/ingest.py`：实现 `POST /ingest` 路由，接收 `file` 或 `text` + `collection` + `strategy` + `metadata.source/category`
- [x] 5.2 流水线实现：解析 → 分块 → 元数据清洗 → Embedding → 写入 Qdrant + SQLite 元数据
- [x] 5.3 实现增量更新：同 `doc_id` 重新 ingest 时按 `content_hash` 判断是否变化，未变化跳过；变化则删除旧 chunk 再写入
- [x] 5.4 返回结构 `{code, message, data: {doc_id, chunk_count, collection}}`
- [x] 5.5 错误处理：`404 COLLECTION_NOT_FOUND` / `422 INGEST_FAILED` / `503 QDRANT_UNAVAILABLE`
- [x] 5.6 编写 `tests/api/test_ingest.py`：覆盖 PDF/MD/TXT 三种格式入库、增量更新跳过、collection 不存在、Qdrant 不可用降级 5 个场景

## 6. 解析 API（POST /parse）

- [x] 6.1 创建 `rag-service/app/api/parse.py`：实现 `POST /parse` 路由，仅解析与分块，不写入向量库
- [x] 6.2 接收 `file` / `text` + `strategy` + `chunk_size` + `chunk_overlap`
- [x] 6.3 返回 `{code, message, data: {doc_id, chunks, layout_summary}}`，layout_summary 含标题数/段落数/表格数/图片数
- [x] 6.4 错误处理：`400 UNSUPPORTED_FORMAT` / `422 PARSE_FAILED` / `507 MODEL_UNAVAILABLE`（OCR/版面模型加载失败时降级为 fixed 分块并返回 warning）
- [x] 6.5 编写 `tests/api/test_parse.py`：覆盖三种格式 + 三种分块策略 + 错误场景

## 7. 检索 API（POST /retrieve）

- [x] 7.1 创建 `rag-service/app/retrieval/embedder.py`：加载 `BAAI/bge-large-zh-v1.5`，提供 `async def embed(texts: list[str]) -> list[list[float]]`
- [x] 7.2 创建 `rag-service/app/retrieval/dense_searcher.py`：Qdrant dense 向量检索，余弦距离，`score_threshold=0.3` 默认过滤
- [x] 7.3 创建 `rag-service/app/retrieval/sparse_searcher.py`：Qdrant sparse 向量检索（BM25），jieba 预分词
- [x] 7.4 创建 `rag-service/app/retrieval/hybrid_searcher.py`：RRF 融合 `score = Σ w_i / (k + rank_i)`，`k=60`，权重 vector 0.7 + bm25 0.3（从 config 读取）
- [x] 7.5 创建 `rag-service/app/retrieval/hyde.py`：HyDE 查询改写，调用一次 LLM 生成假设答案文档；默认关闭，由 `use_hyde=true` 启用
- [x] 7.6 创建 `rag-service/app/api/retrieve.py`：实现 `POST /retrieve` 路由，参数 `query`/`collection`/`mode`/`top_k`/`filters`/`use_hyde`
- [x] 7.7 实现降级：`mode=vector` 时 Embedder 不可用 → 自动切换 bm25 并返回 warning；`mode=hybrid` 时 Embedder 不可用 → 退化为 bm25
- [x] 7.8 返回 `{code, message, data: {results: [{content, score, doc_id, chunk_index, metadata}]}}`
- [x] 7.9 错误处理：`400 INVALID_MODE` / `503 QDRANT_UNAVAILABLE`
- [x] 7.10 编写 `tests/api/test_retrieve.py`：覆盖三种 mode、HyDE 开启/关闭、filters 过滤、Embedder 不可用降级、Qdrant 不可用 503 共 7+ 场景

## 8. 重排 API（POST /rerank）

- [x] 8.1 创建 `rag-service/app/retrieval/reranker.py`：加载 `BAAI/bge-reranker-v2-m3`，提供 `async def rerank(query, documents, top_k) -> list[ScoredDoc]`
- [x] 8.2 服务启动时加载模型，加载失败记录 warning 但不阻塞启动（懒加载策略：首次调用时再尝试加载）
- [x] 8.3 创建 `rag-service/app/api/rerank.py`：实现 `POST /rerank` 路由，参数 `query`/`documents`/`top_k`/`model`
- [x] 8.4 实现降级：模型不可用时按原 score 排序并返回 `warning: "reranker_degraded"`
- [x] 8.5 错误处理：`507 RERANKER_MODEL_UNAVAILABLE`（仅当模型完全无法加载且无原 score 可排序时）
- [x] 8.6 编写 `tests/api/test_rerank.py`：覆盖正常重排、模型不可用降级、空文档列表 3 个场景

## 9. 健康检查（GET /health）

- [x] 9.1 创建 `rag-service/app/api/health.py`：实现 `GET /health` 路由
- [x] 9.2 检查 Qdrant 连通性（执行 `GET /collections` 测试）
- [x] 9.3 检查 Embedder 模型加载状态（lazy load 状态查询）
- [x] 9.4 检查 Reranker 模型加载状态
- [x] 9.5 返回 `{status: "ok" | "degraded", components: {qdrant, embedder, reranker}}`，任一关键组件不可用则 status=degraded（HTTP 200，由主系统决定是否走降级分支）

## 10. 集合与文档管理 API

- [x] 10.1 创建 `rag-service/app/api/collections.py`：实现 `POST /collections`（创建，指定向量维度与距离度量）
- [x] 10.2 实现 `DELETE /collections/{name}`：删除 collection 同时清理 SQLite 元数据
- [x] 10.3 实现 `GET /collections`：列出所有 collection 及其维度、文档数、chunk 数
- [x] 10.4 实现 `GET /collections/{name}/documents`：分页查询 collection 内文档（`page`/`page_size` 参数）
- [x] 10.5 实现 `DELETE /collections/{name}/documents/{doc_id}`：删除指定文档的所有 chunk
- [x] 10.6 编写 `tests/api/test_collections.py`：覆盖 CRUD + 文档查询分页

## 11. 单元测试

- [x] 11.1 `tests/parser/test_pdf_parser.py`：覆盖 PDF 版面分析、表格识别、阅读顺序还原（使用 fixture PDF）
- [x] 11.2 `tests/parser/test_chunker.py`：覆盖三种分块策略 + 元数据清洗规则
- [x] 11.3 `tests/retrieval/test_dense_searcher.py`：mock Qdrant client，验证 score_threshold 过滤
- [x] 11.4 `tests/retrieval/test_hybrid_searcher.py`：验证 RRF 融合公式与权重配置
- [x] 11.5 `tests/retrieval/test_reranker.py`：验证模型加载失败降级路径
- [x] 11.6 `tests/storage/test_qdrant_client.py`：验证 collection 创建/删除/存在性检查
- [x] 11.7 `tests/storage/test_metadata_store.py`：验证 SQLite 元数据 CRUD 与 content_hash 增量更新

## 12. 集成测试

- [x] 12.1 编写端到端测试：PDF 文档 → `/parse` 预览分块 → `/ingest` 入库 → `/retrieve` 检索 → `/rerank` 重排 全流程
- [x] 12.2 编写降级测试：mock Qdrant 不可用 → `/retrieve` 返回 503，`/health` 返回 degraded
- [x] 12.3 编写降级测试：mock Embedder 模型不可用 → `/retrieve` 自动切换 bm25 模式
- [x] 12.4 编写契约测试：定义主系统 RAG Client 期望的响应 schema，验证 rag-service 实际响应一致（为下一个 change 预留）
- [x] 12.5 在 `tests/integration/conftest.py` 提供 `rag_service_client` fixture（httpx.AsyncClient 指向测试实例）

## 13. 部署文档

- [x] 13.1 编写 `rag-service/README.md`：项目定位、架构图、快速启动（docker-compose up）、API 列表、配置说明
- [x] 13.2 编写 `rag-service/docs/api.md`：详细 API 契约（请求/响应示例、错误码、降级规则）
- [x] 13.3 编写 `rag-service/docs/deployment.md`：手动部署、资源占用、模型预下载、与主系统联调步骤
- [x] 13.4 在主系统 `docs/design-spec/01_正式设计/11_RAG服务独立项目设计.md` 第 4 章回写实际目录结构（如有偏差）
- [x] 13.5 在主系统根目录 `docker-compose.yml` 中（如已存在）扩展加入 rag-service 依赖，或新建 `docker-compose.full.yml` 编排三服务（主系统 + rag-service + qdrant）

## 14. 上线前验证

- [x] 14.1 运行 `pytest tests/` 全量测试通过（覆盖率 ≥ 70%）
- [x] 14.2 运行 `ruff check app/ tests/` 无 lint 错误
- [ ] 14.3 `docker-compose up -d` 启动双服务，验证健康检查 `GET /health` 返回 ok
- [ ] 14.4 灌入毕设演示 PDF（10 篇文档），验证 `/parse` `/ingest` `/retrieve` `/rerank` 全链路通畅
- [ ] 14.5 验证 Qdrant 容器停止时 `/retrieve` 返回 503，`/health` 返回 degraded
- [ ] 14.6 验证 Embedder 模型文件删除时 `/retrieve` mode=vector 自动降级为 bm25
- [ ] 14.7 性能验证：单次 `/retrieve` + `/rerank` 在 1000 chunk 规模下响应 < 2 秒

> 14.3-14.7 依赖真实 Docker + Qdrant + 模型权重环境，SOP 见 `rag-service/docs/verification.md`，由用户在 Docker 主机手动执行后回填勾选。
