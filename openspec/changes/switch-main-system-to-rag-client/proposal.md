## Why

v2.0 阶段 1+2 已完成 rag-service 独立项目搭建（见 `add-rag-service-project` change），但主系统仍通过 v1.x 内嵌的 `src/multi_agent_system/tools/knowledge_search.py`（[knowledge_search.py:1](src/multi_agent_system/tools/knowledge_search.py#L1)）直连 Qdrant，存在三个亟待解决的问题：

- **架构割裂**：双项目架构的论文价值无法兑现。rag-service 已独立部署，主系统却在 `tools/knowledge_search.py` 内部维护了一套重复的 Qdrant 连接与检索逻辑（[knowledge_search.py:1](src/multi_agent_system/tools/knowledge_search.py#L1)），代码冗余且职责混乱。
- **降级链路缺失**：当前 `processor_react.py` 与 `coordinator.py` 直接调用 `KnowledgeSearchTool`（[processor_legacy.py:258](src/multi_agent_system/agents/processor_legacy.py#L258)、[coordinator.py:568](src/multi_agent_system/agents/coordinator.py#L568)），rag-service 不可达时无任何降级路径，主流程会因 RAG 调用失败而中断。
- **v1.1 P0 埋点 bug 未修复**：`traces.total_tokens` 永远为 0（[trace.py:141](src/multi_agent_system/core/trace.py#L141)），`add_token_usage` 方法虽已存在（[trace.py:221](src/multi_agent_system/core/trace.py#L221)）但 `_finalize_span` 阶段未调用，导致 Token 成本控制台（开发人员模块）拿不到真实数据。

完整背景与方案见 [docs/design-spec/01_正式设计/11_RAG服务独立项目设计.md](../../docs/design-spec/01_正式设计/11_RAG服务独立项目设计.md) 第 13 章、[02_工单处理流程设计.md](../../docs/design-spec/01_正式设计/02_工单处理流程设计.md) 第 3.1 节、[13_开发人员工作台设计.md](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md) 第 11 章。本 change 聚焦阶段 3（M3）的工作范围——主系统切换到 RAG Client + 源码边界对齐 + P0 埋点修复。

## What Changes

- **新增** 主系统侧 `src/multi_agent_system/tools/rag_client.py`：HTTP 客户端封装 `RagClient` 类与 `RagServiceUnavailable` 异常类。
- **修改** `src/multi_agent_system/agents/processor_react.py`：`_prefetch_knowledge` 方法从调用 `KnowledgeSearchTool` 切换为调用 `RagClient.retrieve` + `RagClient.rerank`。
- **修改** `src/multi_agent_system/api/app.py`：移除 `KnowledgeSearchTool.create_from_settings()` 初始化与 `register_knowledge_tool` 注册（[app.py:53-76](src/multi_agent_system/api/app.py#L53)），改为初始化 `RagClient` 单例注入到 app.state。
- **新增** 降级策略：`RagClient` 调用失败时（连续 3 次失败 / `/health=degraded` / 10 秒超时）抛出 `RagServiceUnavailable`，`processor_react.py` 捕获后走「无知识增强」分支（references=[]，processing_result 仍能生成）。
- **新增** `src/multi_agent_system/models/token_stats.py`：适配版 Token 统计模型（从 explore/farm-manager 复用，去除 farm_id，新增 ticket_id）。
- **新增** `src/multi_agent_system/services/quota_service.py`：月/周配额检查服务（详见 [12_Token成本控制台设计.md](../../docs/design-spec/01_正式设计/12_Token成本控制台设计.md)）。
- **新增** `src/multi_agent_system/api/admin_stats.py`：开发人员模块 Token 查询 API（`/tokens`、`/tokens/daily`、`/tokens/hourly`、`/quota/{user_id}`）。
- **修改** `users` 表新增字段：`token_monthly_limit`、`token_weekly_limit`（INT 默认值）。
- **修改** `src/multi_agent_system/core/trace.py`：`_finalize_span`（即 `SpanContext.__aexit__`，[trace.py:90](src/multi_agent_system/core/trace.py#L90)）阶段调用 `add_token_usage` 累加 trace token；新增 `_accumulate_token_daily_stats` 写入 `token_daily_stats` 表。
- **修复** v1.1 的 5 个 P0 埋点问题（详见 [13_开发人员工作台设计.md](../../docs/design-spec/01_正式设计/13_开发人员工作台设计.md) 第 11 章）：
  - Memory 加载独立 span
  - RAG 检索 hit_count/top_score 写入 rag_stats
  - retry_check/should_review 决策子结构化
  - classify span reason 字段
  - traces.total_tokens 修复（如上）

## Capabilities

### New Capabilities

- `rag-client-integration`: 主系统 RAG Client — HTTP 调用 rag-service 的 `/retrieve` 与 `/rerank`，封装超时、重试、降级触发逻辑；提供 `RagServiceUnavailable` 异常类供上层捕获。
- `rag-fallback-strategy`: RAG 降级策略 — rag-service 不可达时 ReActProcessorAgent 走「无知识增强」分支，保证工单主流程不中断。
- `token-stats-accumulation`: Token 累加修复 — `_finalize_span` 阶段调用 `add_token_usage`，并新增 `_accumulate_token_daily_stats`；适配 farm-manager 的 Token 统计模型与配额服务。

### Modified Capabilities

无。`openspec/specs/` 当前为空，本变更为首次引入 capability。

## Impact

- **后端代码**：
  - `src/multi_agent_system/tools/rag_client.py`：新增（约 200 行）
  - `src/multi_agent_system/agents/processor_react.py`：修改 `_prefetch_knowledge` 方法（约 50 行 diff）
  - `src/multi_agent_system/agents/processor_legacy.py`：同步修改（约 30 行 diff，若仍存在）
  - `src/multi_agent_system/agents/coordinator.py`：修改 RAG 调用点（[coordinator.py:568](src/multi_agent_system/agents/coordinator.py#L568)）
  - `src/multi_agent_system/api/app.py`：移除 KnowledgeSearchTool 初始化（[app.py:53-76](src/multi_agent_system/api/app.py#L53)），新增 RagClient 单例
  - `src/multi_agent_system/core/trace.py`：`SpanContext.__aexit__` 调用 `add_token_usage`（[trace.py:90](src/multi_agent_system/core/trace.py#L90)）；新增 `_accumulate_token_daily_stats` 方法
  - `src/multi_agent_system/models/token_stats.py`：新增（Pydantic 模型 TokenDailyStats、TokenHourlyStats、QuotaInfo）
  - `src/multi_agent_system/services/quota_service.py`：新增（约 150 行）
  - `src/multi_agent_system/api/admin_stats.py`：新增（4 个端点）
  - `src/multi_agent_system/models/user.py`（或 db.py）：users 表新增字段
  - `src/multi_agent_system/core/database.py`：`_SCHEMA_SQL` 新增 `token_daily_stats` 表与 users 表字段
- **保留代码**（不删除）：
  - `src/multi_agent_system/tools/knowledge_search.py`：作为降级备份保留，毕设答辩演示「双项目 + 降级」时可作为对照
  - `src/multi_agent_system/tools/knowledge_tool_adapter.py`：保留但默认不注册（通过配置项控制）
- **配置变更**：
  - `config.yaml` 新增 `rag_service.url`（默认 `http://localhost:8001`）、`rag_service.timeout`（默认 10）、`rag_service.retry`（默认 1）、`rag_service.health_check_interval`（默认 300）
- **依赖**：新增 `httpx`（主系统原已有，无需新增）
- **数据库迁移**：
  - 新增表 `token_daily_stats`（按 user_id + date 聚合）
  - `users` 表新增两列：`token_monthly_limit`（INT 默认 100000）、`token_weekly_limit`（INT 默认 25000）
  - SQLite 场景通过 schema 初始化 `CREATE TABLE IF NOT EXISTS` 自动迁移
- **文档**：
  - `docs/design-spec/01_正式设计/02_工单处理流程设计.md` 第 3.1 节已在 v2.0 沉淀
  - `docs/design-spec/01_正式设计/13_开发人员工作台设计.md` 第 11 章 P0 修复方案同步
- **测试**：RAG Client 单测、降级集成测试、Token 累加正确性测试，预估工时 2 天
