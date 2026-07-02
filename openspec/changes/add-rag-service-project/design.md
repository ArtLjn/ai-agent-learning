## Context

v1.x 阶段 RAG 能力内嵌于主系统 `src/multi_agent_system/tools/knowledge_search.py`，仅实现了「固定窗口分块 + Qdrant 余弦检索」的最小流程，论文无法独立成章，算法演进也耦合在主系统发布周期内。v2.0 将 RAG 能力抽离为独立项目 `rag-service`，目标包括：论文独立成章、对外可复用、算法独立演进、复用 NSQA 项目 PDF 复杂解析资产、与主系统职责切分清晰。

完整 14 章设计已沉淀到 [docs/design-spec/01_正式设计/11_RAG服务独立项目设计.md](../../docs/design-spec/01_正式设计/11_RAG服务独立项目设计.md)。本 design.md 聚焦「为什么这样实现」的关键决策，避免重复 design-spec 内容。

约束：

- 独立项目位于 `/Users/ljn/Documents/demo/finished/rag-service/`，与 `ai-agent-learning` 同级
- 主系统通过 `tools/rag_client.py` HTTP 调用（HTTP client 在另一个 change 中实现）
- 毕设范围：不做多租户、计费、知识图谱、自训练模型、分布式部署
- 复用 NSQA 项目 PDF 复杂解析代码（模型权重、版面分析、表格识别从 NSQA 迁移）
- 与主系统复用主库 MySQL 不允许，rag-service 自带 SQLite 元数据存储

## Goals / Non-Goals

**Goals:**

- 从零搭建可独立启动的 rag-service 项目，5 个核心 API 全通
- PDF 复杂解析（版面 + 表格 + 公式 + 阅读顺序）从 NSQA 迁移并适配本系统元数据 schema
- 提供向量 / BM25 / 混合（RRF）三种检索模式，默认 hybrid
- Cross-Encoder 重排（BAAI/bge-reranker-v2-m3），加载失败可降级
- 完整的降级链路：Qdrant 不可用返回 503，Embedder 不可用退化为 bm25，重排不可用按原 score 排序
- 单机 4 核 8G 可部署，docker-compose 一键启动

**Non-Goals:**

- 不做 RAG 评估平台（仅留接口与最小评估脚本，P2 范围）
- 不做主系统 RAG Client 实现（在 `switch-main-system-to-rag-client` change 中完成）
- 不做主系统降级策略实施（同上）
- 不做 HyDE 默认启用（默认关闭，由调用方按工单类别决定）
- 不做多租户与权限隔离
- 不做自训练 / 微调 Embedding 与重排模型
- 不做分布式部署与读写分离

## Decisions

### Decision 1: 技术栈选择 — FastAPI + Qdrant + sentence-transformers

**选择**：FastAPI（与主系统一致）+ Qdrant（同时支持 dense 与 sparse 向量索引）+ sentence-transformers（加载 BAAI 系列模型）。

**理由**：

- FastAPI 与主系统技术栈统一，便于代码风格、依赖管理、错误处理模式复用
- Qdrant 原生支持 sparse 向量（内置 BM25 score），不需要单独维护 Elasticsearch 或 rank_bm25 索引，单库统一管理
- sentence-transformers 加载 BAAI/bge-large-zh-v1.5（Embedding）与 BAAI/bge-reranker-v2-m3（重排），同系列 tokenizer 与向量维度对齐方便，中英双语支持
- Qdrant 内存占用与 collection 规模相关，毕设 demo 数据（万级 chunk）约 500MB，单机可承载

**替代方案考虑**：

- Milvus：功能更强但部署更重，毕设场景过度
- PostgreSQL + pgvector：单库简化部署，但 sparse 向量支持不如 Qdrant 原生
- 自维护 Elasticsearch 做 BM25：多引入一个组件，运维负担增加

### Decision 2: 元数据存储 — 独立 SQLite vs 复用主系统 MySQL

**选择**：rag-service 自带 SQLite（`storage/metadata_store.py`），不复用主系统 MySQL。

**理由**：

- 主系统 MySQL 仅存工单、用户、审核等业务数据，RAG 内部状态（collection 元信息、文档版本、content_hash）属于另一类关注点
- 独立 SQLite 让 rag-service 可独立部署给其他项目复用，不强制依赖外部 MySQL
- 元数据规模小（万级文档），SQLite 完全够用
- 避免主系统数据库被 RAG 内部状态污染，边界清晰

**替代方案考虑**：

- 复用主系统 MySQL + 新增表：违反「rag-service 对外可复用」目标，部署耦合
- 用 Qdrant payload 存元数据：Qdrant 不擅长关系查询与版本管理

### Decision 3: PDF 复杂解析 — 从 NSQA 迁移 vs 自研

**选择**：从 NSQA 项目（用户的其他仓库）迁移 PDF 复杂解析代码，按本系统元数据 schema 做适配，不自研。

**理由**：

- NSQA 已验证的 PDF 处理架构（版面分析、表格识别、阅读顺序还原、公式 LaTeX 还原）是本 change 最有论文价值的部分
- 自研版面分析与表格识别需要大量训练数据与调参，远超毕设时间预算
- 迁移后只做收敛（去除 NSQA 业务相关代码、统一 chunk metadata 字段），保留核心算法

**迁移范围**：

- 版面分析网络（输出 title/paragraph/table/figure/formula 类别与 bbox）
- 表格识别（行/列/合并单元格结构化，输出 HTML/Markdown 表格）
- 文本结构化（段落合并、阅读顺序还原、公式 LaTeX OCR）
- 智能分块三种策略：structure_aware / semantic / fixed
- 元数据清洗（去断行符、统一空白、剔除 OCR 噪声）

**收敛点**：

- chunk metadata 统一为 `{source, page, category, heading_path, doc_id, chunk_index}`
- 去除 NSQA 中知识图谱构建相关代码（已与导师澄清 KG 暂搁置）

### Decision 4: 检索默认模式 — hybrid (RRF) + 可切换

**选择**：`/retrieve` 默认 `mode=hybrid`，使用 RRF 融合（k=60，向量权重 0.7 + BM25 权重 0.3），同时支持 `mode=vector` 和 `mode=bm25` 显式指定。

**理由**：

- 混合检索兼顾语义召回与关键词命中，对工单查询（混合自然语言 + 错误码）效果最佳
- RRF 公式 `score = Σ w_i / (k + rank_i)` 简单稳定，权重可在 config 中调整
- 长查询（>15 字）调高向量权重到 0.8，短查询或带错误码调高 BM25 到 0.5，调用方按工单类别灵活配置
- 显式支持单模式便于论文实验对比（向量 vs BM25 vs 混合的召回率）

**替代方案考虑**：

- 默认纯向量：对错误码、产品型号等关键词查询表现差
- 默认纯 BM25：对自然语言工单描述召回弱
- 学习 to rank：毕设范围无标注数据，过度

### Decision 5: Cross-Encoder 重排 — 加载到内存 + 可降级

**选择**：Cross-Encoder `BAAI/bge-reranker-v2-m3` 在服务启动时加载到内存，对 `/retrieve` 召回的 top-20 重排取 top-5；模型加载失败时 `/rerank` 降级为按原 score 排序并返回 warning 标记。

**理由**：

- Cross-Encoder 精度显著高于 Bi-Encoder（query 与 doc 拼接后输入 Transformer），适合精排阶段
- 与 Embedding 同为 BAAI 系列，tokenizer 与维度对齐方便
- 显存约 2GB，单卡 4G 或 CPU 推理可接受（CPU 单次重排 20 文档约 800ms）
- 加载失败时降级而非 500 错误，让主系统能继续工作（宁可次优也不要直接失败）

**替代方案考虑**：

- 调用 Cohere / Jina 商用 reranker API：依赖外部服务，毕设场景不必要
- 仅用 Bi-Encoder 不做重排：精度损失明显
- 自训练 reranker：毕设范围过度

### Decision 6: API 契约风格 — 统一返回结构 + 细粒度错误码

**选择**：所有接口统一返回 `{ "code": "OK" | "FAILED", "message": str, "data": any }`，错误码使用 HTTP 状态码 + 大写业务码（如 `422 PARSE_FAILED` / `503 QDRANT_UNAVAILABLE` / `507 RERANKER_MODEL_UNAVAILABLE`）。

**理由**：

- 与主系统 API 风格一致（`code/message/data`），主系统 RAG Client 解析逻辑可复用
- 业务码采用大写 + 下划线，便于日志检索与告警规则匹配
- HTTP 状态码语义清晰：4xx 客户端错误、5xx 服务端错误、507专门用于模型不可用（区别于 503 服务不可用）

**降级返回策略**：

- Qdrant 不可用：503 + `QDRANT_UNAVAILABLE`，让主系统走无 RAG 分支
- Embedder 不可用：自动降级 mode 到 bm25，200 + warning 字段
- Reranker 不可用：按原 score 排序，200 + warning 字段
- PDF 版面模型不可用：降级为 fixed 分块，200 + warning 字段

### Decision 7: 部署方式 — docker-compose 双服务编排

**选择**：`docker-compose.yml` 编排 rag-service + qdrant 两个服务，qdrant 数据卷持久化，`make dev` 一键启动。

**理由**：

- 单机部署最简化，毕设答辩演示门槛低
- qdrant 数据卷持久化避免每次重启重新 ingest
- rag-service 通过环境变量 `QDRANT_URL=http://qdrant:6333` 引用，本地开发也可切换为 `localhost:6333`
- 主系统 docker-compose 在另一个 change 中扩展加入 rag-service 依赖

**资源预估**：

- rag-service 容器：~2.5GB（含模型加载）
- qdrant 容器：~500MB（毕设数据规模）
- 单机 4 核 8G 可承载双容器 + 主系统

## Risks / Trade-offs

- **[风险] PDF 复杂解析迁移复杂度超预期** → 缓解：优先迁移 MD/TXT 简单解析，PDF 复杂解析作为 P1；保留主系统 `personal_knowledge_base` 作为降级备份方案；如阶段 1 第 2 天 PDF 迁移阻塞，先发布仅支持 MD/TXT 的 MVP
- **[风险] Cross-Encoder 首次加载耗时长（下载模型 ~ 1GB）** → 缓解：Dockerfile 中预下载模型到镜像；首次启动记录 warning 日志
- **[风险] Qdrant sparse 向量索引在大规模 collection 上性能下降** → 缓解：毕设 demo 数据万级 chunk，性能足够；论文实验记录数据规模与延迟关系
- **[风险] sentence-transformers 与 PyTorch 版本兼容性** → 缓解：requirements.txt 锁定版本，CI 跑依赖一致性检查
- **[折中] 不做评估平台完整化** → 仅留 `/evaluation/run` 接口与最小评估脚本，论文实验数据手工采集
- **[折中] 不做 HyDE 默认启用** → 增加约 1 次 LLM 调用延迟，由调用方按工单类别决定；毕设范围默认关闭
- **[折中] SQLite 元数据不支持并发写** → 单进程 FastAPI + asyncio 场景下无问题；展望章节说明分布式场景需迁移到 PostgreSQL

## Migration Plan

**部署步骤**：

1. 创建 `/Users/ljn/Documents/demo/finished/rag-service/` 目录与项目骨架
2. 实现 parser/retrieval/storage/api/core 各模块
3. `docker-compose up -d` 启动 rag-service + qdrant 双服务
4. 调用 `POST /collections` 创建初始 collection（如 `ticket_knowledge`）
5. 通过 `POST /ingest` 灌入毕设演示用的 PDF / MD 知识库文档
6. 调用 `POST /retrieve` 验证检索效果
7. 调用 `POST /rerank` 验证重排效果

**与主系统对接**：

- 本 change 完成后主系统暂不切换，仍使用 `tools/knowledge_search.py`
- 下一个 change `switch-main-system-to-rag-client` 实施时，主系统切换到 `tools/rag_client.py` 调用本服务
- 切换期间 `tools/knowledge_search.py` 保留作为降级备份

**回滚策略**：

- rag-service 项目独立，回滚仅需停止 docker-compose
- Qdrant 数据卷可保留或删除（`docker-compose down -v`）

**兼容性**：

- 主系统无任何修改，完全向后兼容
- rag-service 不依赖主系统任何代码或数据库

## Open Questions

无未决问题。所有关键决策已在 design-spec 第 1-14 章与本文档明确。如实现过程中遇到以下情况需重新讨论：

- NSQA 项目代码迁移后发现版面分析模型权重过大（> 2GB） → 考虑改用更轻量的 PaddleOCR PP-Structure
- Qdrant sparse 向量索引在毕设数据规模上效果不达预期 → 回退到 rank_bm25 + 自维护倒排表
- Cross-Encoder CPU 推理延迟超过 2 秒 → 论文实验中明确记录，展望章节讨论 GPU 加速方案
