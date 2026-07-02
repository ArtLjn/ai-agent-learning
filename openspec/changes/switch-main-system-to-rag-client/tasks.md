## 1. RAG Client 实现

- [ ] 1.1 创建 `src/multi_agent_system/tools/rag_client.py`：定义 `RagClient` 类与 `RagServiceUnavailable` 异常类
- [ ] 1.2 实现 `RagClient.__init__(base_url, timeout=10, max_retries=1, retry_interval=0.5, cooldown_seconds=300)`：初始化 httpx.AsyncClient、`_failure_count=0`、`_unavailable_until=None`
- [ ] 1.3 实现 `async def retrieve(query, collection, mode="hybrid", top_k=10, filters=None, use_hyde=False) -> list[dict]`：调用 rag-service `POST /retrieve`
- [ ] 1.4 实现 `async def rerank(query, documents, top_k=5, model=None) -> list[dict]`：调用 rag-service `POST /rerank`
- [ ] 1.5 实现 `async def retrieve_and_rerank(query, collection, top_k=20, final_top_k=5) -> list[dict]`：组合 retrieve + rerank 两阶段调用便捷方法
- [ ] 1.6 实现 `async def health() -> dict`：调用 rag-service `GET /health`，返回 `{status, components}`
- [ ] 1.7 实现 10 秒超时 + 1 次重试（间隔 500ms）逻辑：仅网络错误重试，5xx 不重试直接抛
- [ ] 1.8 实现降级触发：连续 3 次失败 → 设置 `_unavailable_until = now + cooldown_seconds`；调用前检查冷却期，过期则尝试一次
- [ ] 1.9 在 `src/multi_agent_system/config.py` 新增配置项：`rag_service.url`、`rag_service.timeout`、`rag_service.retry`、`rag_service.cooldown_seconds`、`legacy_knowledge_tool_enabled`（默认 false）

## 2. ReActProcessorAgent 切换调用

- [ ] 2.1 修改 `src/multi_agent_system/agents/processor_react.py` 的 `_prefetch_knowledge` 方法（约 [processor_react.py:167](src/multi_agent_system/agents/processor_react.py#L167)）：从调用 `self._knowledge_tool.search()` 改为调用 `self._rag_client.retrieve_and_rerank()`
- [ ] 2.2 修改构造函数：新增 `rag_client: RagClient | None = None` 参数，保留 `knowledge_tool` 参数向后兼容
- [ ] 2.3 同步修改 `src/multi_agent_system/agents/processor_legacy.py`（[processor_legacy.py:258](src/multi_agent_system/agents/processor_legacy.py#L258)）的 RAG 调用点
- [ ] 2.4 同步修改 `src/multi_agent_system/agents/coordinator.py`（[coordinator.py:568](src/multi_agent_system/agents/coordinator.py#L568)）的 RAG 调用点
- [ ] 2.5 修改 `src/multi_agent_system/api/app.py`：移除 `KnowledgeSearchTool.create_from_settings()` 初始化（[app.py:53-59](src/multi_agent_system/api/app.py#L53)），新增 `RagClient(settings.rag_service_url, ...)` 单例注入 `app.state.rag_client`
- [ ] 2.6 修改 `app.py` 的 `register_knowledge_tool` 调用（[app.py:76](src/multi_agent_system/api/app.py#L76)）：仅当 `legacy_knowledge_tool_enabled=true` 时注册，默认跳过
- [ ] 2.7 修改 `app.py` 的 `ReActProcessorAgent` 实例化（[app.py:83-88](src/multi_agent_system/api/app.py#L83)）：传入 `rag_client=app.state.rag_client`

## 3. 降级策略实现

- [ ] 3.1 在 `processor_react.py` 的 `_prefetch_knowledge` 方法包裹 `try/except RagServiceUnavailable`
- [ ] 3.2 异常捕获后返回空字符串（`knowledge_context=""`），让 ReAct 主循环仅基于工单内容生成方案
- [ ] 3.3 在工单的 `metadata.rag_stats` 写入 `{hit_count: 0, rag_service_reachable: false, retrieval_mode: "degraded"}`
- [ ] 3.4 `references_json` 字段降级为空数组 `[]`，但 `processing_result` MUST 仍能正常生成
- [ ] 3.5 记录 warning 日志：`logger.warning(f"rag_service_unavailable ticket_id={ticket_id} fallback_to_no_knowledge")`
- [ ] 3.6 创建降级场景的 trace span：`span_type=tool_call`、`name=rag_retrieve`、`status=degraded`、`metadata.rag_stats` 反映降级

## 4. Token 累加修复

- [ ] 4.1 修改 `src/multi_agent_system/core/trace.py` 的 `SpanContext.__aexit__`（[trace.py:90](src/multi_agent_system/core/trace.py#L90)）：在 span_type 为 `llm_call` 且 `output_data.tokens` 非空时，调用 `self._manager.add_token_usage(trace_id, delta)`
- [ ] 4.2 实现 token delta 计算：`delta = output_data.tokens.get("total", 0)`
- [ ] 4.3 在 `TraceManager` 新增 `async def _accumulate_token_daily_stats(self, trace_id, user_id, model_name, prompt_tokens, completion_tokens, total_tokens)` 方法：写入 `token_daily_stats` 表
- [ ] 4.4 `__aexit__` 累加 token 时同步触发 `_accumulate_token_daily_stats`（异步触发不阻塞 span 关闭）
- [ ] 4.5 修复 `traces.total_tokens` 永远为 0 的 P0 bug：验证 `add_token_usage` SQL `UPDATE traces SET total_tokens = total_tokens + ?` 在 `__aexit__` 后实际执行（[trace.py:227](src/multi_agent_system/core/trace.py#L227)）
- [ ] 4.6 在 `core/database.py` 的 `_SCHEMA_SQL` 新增 `token_daily_stats` 表（user_id + date + model_name 联合主键，字段：prompt_tokens、completion_tokens、total_tokens、ticket_id 可选）

## 5. Token 统计模型（适配版）

- [ ] 5.1 创建 `src/multi_agent_system/models/token_stats.py`：定义 `TokenDailyStats`、`TokenHourlyStats`、`QuotaInfo`、`TokenUsageRecord` Pydantic 模型
- [ ] 5.2 字段设计：去除 farm-manager 原版的 `farm_id`，新增 `ticket_id`（可选）、保留 `user_id`、`model_name`、`prompt_tokens`、`completion_tokens`、`total_tokens`、`date`
- [ ] 5.3 在 `core/database.py` 新增 `token_daily_stats` 表的 CRUD 方法：`record_token_usage`、`get_daily_stats_by_user`、`get_hourly_stats_by_user`、`get_user_quota_status`

## 6. 配额服务

- [ ] 6.1 创建 `src/multi_agent_system/services/quota_service.py`：定义 `QuotaService` 类
- [ ] 6.2 实现 `async def check(self, user_id) -> QuotaInfo`：返回 `{monthly_used, monthly_limit, weekly_used, weekly_limit, status: "ok"|"warning"|"exceeded"}`
- [ ] 6.3 实现 `async def update_limits(self, user_id, monthly_limit=None, weekly_limit=None)`：管理员调整配额
- [ ] 6.4 在 `users` 表新增字段：`token_monthly_limit`（INT 默认 100000）、`token_weekly_limit`（INT 默认 25000）
- [ ] 6.5 在 `core/database.py` 的 `_SCHEMA_SQL` 更新 `users` 表 schema，迁移逻辑用 `ALTER TABLE ADD COLUMN`（启动时检查列存在性）
- [ ] 6.6 实现 `status` 判定逻辑：使用量 < 80% → ok；80%-100% → warning；> 100% → exceeded
- [ ] 6.7 配额检查不阻断主流程，超限时仅记日志与推送 WebSocket 事件（前端开发人员模块订阅）

## 7. Token 查询 API

- [ ] 7.1 创建 `src/multi_agent_system/api/admin_stats.py`：开发人员模块 Token 端点
- [ ] 7.2 实现 `GET /admin/tokens`：查询参数 `user_id`/`start_date`/`end_date`/`model_name`，返回 token_daily_stats 列表
- [ ] 7.3 实现 `GET /admin/tokens/daily`：返回按日聚合的 token 消耗（最近 30 天）
- [ ] 7.4 实现 `GET /admin/tokens/hourly`：返回按小时聚合（最近 24 小时），便于热点分析
- [ ] 7.5 实现 `GET /admin/quota/{user_id}`：返回 `QuotaInfo`
- [ ] 7.6 实现 `PUT /admin/quota/{user_id}`：管理员调整配额（需要管理员权限）
- [ ] 7.7 在 `api/app.py` 注册 `admin_stats` 路由
- [ ] 7.8 错误处理：404 `USER_NOT_FOUND`、403 `FORBIDDEN_NOT_ADMIN`

## 8. 决策点 P0 埋点修复

- [ ] 8.1 **Memory 加载独立 span**：在 `processor_react.py` 调用 `MemoryManager.load_memory()` 处包裹独立 span，`span_type=node`、`name=memory_load`、记录加载的 memory 条数与耗时
- [ ] 8.2 **RAG 检索 hit_count/top_score**：在 `processor_react.py` 的 RAG 调用后，将 `{hit_count, top_score, retrieval_mode, rag_service_reachable}` 写入 process span 的 `metadata.rag_stats`
- [ ] 8.3 **retry_check 决策子结构**：修改 `workflow/graph.py` 的 `retry_check` 节点，在 span `output_data.decision` 写入 `{decision: "retry"|"escalate", reason: "review_score_below_threshold"|"max_retries_exceeded", retry_count, max_retries, current_score}`
- [ ] 8.4 **should_review 决策子结构**：修改路由决策节点的 span，在 `output_data.decision` 写入 `{decision: "process"|"escalate"|"human_review", reason: "...", trigger_conditions: [...]}`
- [ ] 8.5 **classify span reason 字段**：修改 `agents/classifier.py` 在 span `output_data.reason` 写入分类理由文本（不仅是 category/priority）
- [ ] 8.6 **traces.total_tokens 修复**：见任务组 4，验证 `add_token_usage` 在 span 关闭后实际累加

## 9. RAG Client 单元测试

- [ ] 9.1 创建 `tests/tools/test_rag_client.py`，使用 `httpx.MockTransport` mock rag-service 响应
- [ ] 9.2 测试正常 retrieve 调用返回结果解析正确
- [ ] 9.3 测试正常 rerank 调用返回结果解析正确
- [ ] 9.4 测试 10 秒超时触发 `RagServiceUnavailable`
- [ ] 9.5 测试网络错误重试 1 次后仍失败 → 累加 `_failure_count`
- [ ] 9.6 测试连续 3 次失败后 `_unavailable_until` 被设置，后续调用直接抛异常不发起 HTTP
- [ ] 9.7 测试 5xx 错误不重试直接抛
- [ ] 9.8 测试 health 检查返回 `{status, components}` 正确解析
- [ ] 9.9 测试冷却期过后首次调用成功 → `_failure_count` 重置为 0

## 10. 降级集成测试

- [ ] 10.1 创建 `tests/integration/test_rag_fallback.py`：mock rag-service 关闭场景
- [ ] 10.2 测试 rag-service 不可达时 ReActProcessorAgent 走无知识增强分支：`references_json=[]`、`processing_result` 仍能生成
- [ ] 10.3 测试工单完整流程不中断：classify → route → process（降级）→ review → notify → complete
- [ ] 10.4 测试 trace span 正确记录降级：`tool_call` span 的 `metadata.rag_stats.rag_service_reachable=false`
- [ ] 10.5 测试 rag-service 恢复后主系统自动切回正常路径（冷却期过后）

## 11. Token 累加正确性测试

- [ ] 11.1 创建 `tests/core/test_trace_token_accumulation.py`
- [ ] 11.2 测试 `llm_call` span 关闭后 `traces.total_tokens` 正确累加（修复 P0 bug 的核心验证）
- [ ] 11.3 测试 `node`/`tool_call` span 不触发 token 累加
- [ ] 11.4 测试 `output_data.tokens.total=0` 时不累加
- [ ] 11.5 测试 `token_daily_stats` 表正确写入（按 user_id + date + model_name 聚合）
- [ ] 11.6 测试同一 trace 多个 llm_call span 的 token 正确汇总到 traces.total_tokens
- [ ] 11.7 测试配额服务 `QuotaService.check(user_id)` 返回的 status 在不同使用量阈值下正确

## 12. 上线前验证

- [ ] 12.1 运行 `pytest tests/` 全量测试通过
- [ ] 12.2 运行 `ruff check src/ tests/` 无 lint 错误
- [ ] 12.3 启动主系统 + rag-service 双服务，提交一条工单验证完整流程
- [ ] 12.4 关闭 rag-service，提交工单验证降级分支生效（references=[] 但 processing_result 有内容）
- [ ] 12.5 验证 `GET /admin/tokens/daily` 返回真实 token 数据（不再为 0）
- [ ] 12.6 验证 Trace 决策树前端展示 5 个 P0 修复点（memory_load span、rag_stats、decision 子结构、classify reason、total_tokens）
- [ ] 12.7 验证 `legacy_knowledge_tool_enabled=true` 时旧工具仍可作为降级备份启用
- [ ] 12.8 性能验证：rag-service 正常时单工单处理延迟增加 < 3 秒（HTTP 调用 + 重排）
