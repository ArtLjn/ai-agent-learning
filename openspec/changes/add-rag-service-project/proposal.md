## Why

v1.x 阶段 RAG 能力内嵌于主系统 `src/multi_agent_system/tools/knowledge_search.py`（[knowledge_search.py:1](src/multi_agent_system/tools/knowledge_search.py#L1)），仅实现了「固定窗口分块 + Qdrant 余弦检索」的基础流程，论文价值有限；同时答辞反馈提出"RAG 应作为可独立演进、可对外复用的服务"，需要把 RAG 抽成独立项目承载更完整的算法论述（PDF 复杂解析、混合检索、Cross-Encoder 重排）。

主系统当前面临三个具体问题：

- 论文章节单薄：RAG 仅是主系统一个工具模块，无法独立成章。
- 算法演进耦合：每次更换 Embedding 模型、引入 RRF 融合或重排模型，都要改主系统代码并重新部署主链路。
- 资产未能复用：NSQA 项目已验证的 PDF 复杂解析架构（版面分析、表格识别、阅读顺序还原）停留在另一个仓库，无法对外提供服务。

完整 14 章设计已沉淀到 [docs/design-spec/01_正式设计/11_RAG服务独立项目设计.md](../../docs/design-spec/01_正式设计/11_RAG服务独立项目设计.md)。本 change 聚焦阶段 1+2（M1+M2）的工作范围——从零搭建 rag-service 项目并提供 5 个核心 API。

## What Changes

- **新增** 独立项目 `rag-service/`（与 `ai-agent-learning` 同级目录），FastAPI + Qdrant 双服务架构，端口 8001。
- **新增** 项目骨架：`pyproject.toml`、`Dockerfile`、`docker-compose.yml`（编排 rag-service + qdrant）、`.env.example`、`README.md`。
- **新增** `app/parser/` 模块：从 NSQA 项目迁移 PDF 复杂解析（版面分析、表格识别、文本结构化、智能分块、元数据清洗），同时支持 Markdown / TXT。
- **新增** `app/retrieval/` 模块：Embedding 客户端、Dense 向量检索、Sparse BM25 检索、RRF 混合融合、Cross-Encoder 重排、HyDE 查询改写。
- **新增** `app/storage/` 模块：Qdrant 连接封装、collection 管理、SQLite 元数据与版本管理。
- **新增** `app/api/` 模块：5 个 HTTP API（`/parse` `/ingest` `/retrieve` `/rerank` `/health`）+ collection / document 管理 API。
- **新增** `app/core/` 公共能力：`config.py` 集中配置、`logging.py`、`exceptions.py`、`metrics.py`。
- **新增** `app/models/` Pydantic 数据模型：`chunk.py` `query.py` `document.py`。
- **新增** `tests/` 镜像源码结构的 pytest 测试套件。
- **新增** 独立部署文档 `rag-service/README.md`。

## Capabilities

### New Capabilities

- `rag-document-parsing`: 复杂文档解析能力 — PDF 版面分析、表格识别、文本结构化、智能分块（structure_aware/semantic/fixed）、元数据清洗，支持 PDF/MD/TXT 三种格式。
- `rag-hybrid-retrieval`: 混合检索能力 — Dense 向量检索（BAAI/bge-large-zh-v1.5）、Sparse BM25 检索、RRF 融合（vector 0.7 + bm25 0.3）、HyDE 查询改写（可选）。
- `rag-reranking`: Cross-Encoder 重排能力 — BAAI/bge-reranker-v2-m3，对 top-20 候选精排取 top-5，模型加载失败时降级为按原 score 排序。
- `rag-ingestion`: 文档入库能力 — 解析→分块→向量化→Qdrant 写入全链路，支持增量更新（按 content_hash 判等）与版本记录。
- `rag-service-health`: 健康检查与降级能力 — `/health` 暴露 qdrant/embedder/reranker 三组件状态，任一不可用返回 `degraded`，Qdrant 不可用返回 503。

### Modified Capabilities

无。本 change 创建独立项目，不影响主系统现有 capabilities。

## Impact

- **新项目代码**（位于 `/Users/ljn/Documents/demo/finished/rag-service/`）：
  - `rag-service/app/api/`：6 个路由文件（parse/ingest/retrieve/rerank/collections/health）
  - `rag-service/app/parser/`：base、pdf_parser、markdown_parser、text_parser、layout/、table/、chunker/、cleaner
  - `rag-service/app/retrieval/`：embedder、dense_searcher、sparse_searcher、hybrid_searcher、reranker、hyde
  - `rag-service/app/storage/`：qdrant_client、collection_manager、metadata_store（SQLite）、version_manager
  - `rag-service/app/core/`：config、logging、exceptions、metrics
  - `rag-service/app/models/`：chunk、query、document
  - `rag-service/app/main.py`：FastAPI 入口
- **基础设施**：
  - `rag-service/Dockerfile`：Python 3.11 + 系统依赖（OCR/版面分析 native libs）
  - `rag-service/docker-compose.yml`：rag-service + qdrant 服务编排，qdrant 数据卷持久化
  - `rag-service/.env.example`：PORT、QDRANT_URL、EMBEDDING_MODEL、RERANKER_MODEL 等 10 项配置
- **依赖**：
  - 新增 Python 包：`fastapi`、`uvicorn`、`qdrant-client`、`sentence-transformers`、`PyMuPDF`、`pytesseract`、`rank-bm25`、`jieba`、`httpx`
  - 系统 OCR 依赖：`tesseract-ocr` + 中文语言包（Dockerfile 内安装）
- **资源占用**：
  - Qdrant：毕设 demo 数据规模约 500MB 内存
  - Cross-Encoder 模型：加载约 2GB 内存，CPU 推理单次重排约 800ms
  - 总体最低 4 核 8G 可单机部署
- **文档**：`docs/design-spec/01_正式设计/11_RAG服务独立项目设计.md` 已在 v2.0 同步沉淀；本 change 实施后回写实际目录结构与 API 示例
- **测试**：parser/retrieval/storage/api 各模块单测 + 与主系统联调的契约测试（mock 主系统调用），预估工时 4-5 天
