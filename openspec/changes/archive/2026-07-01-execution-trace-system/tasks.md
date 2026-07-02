## 1. SQLite 表结构与 TraceManager 核心

- [ ] 1.1 在 `core/database.py` 的 `_init_tables()` 中新增 `traces` 和 `spans` 表的 CREATE TABLE 语句，包含所有字段和索引
- [ ] 1.2 在 `core/database.py` 中新增 `save_trace()`、`update_trace()`、`get_trace_by_ticket()`、`list_traces()`、`get_trace_stats()` 方法
- [ ] 1.3 在 `core/database.py` 中新增 `save_span()`、`update_span()`、`get_spans_by_trace()`、`get_span_duration_stats()` 方法
- [ ] 1.4 创建 `core/trace.py`，实现 `TraceManager` 类：`start_trace()`、`finish_trace()`、`start_span()` async context manager
- [ ] 1.5 在 `TraceManager` 中实现 `SpanContext` 类：`span_id`、`set_output()`、`set_metadata()`、`set_status()`，支持 `__aenter__`/`__aexit__` 自动记录时长和异常
- [ ] 1.6 编写 `tests/core/test_trace.py`，测试 trace 生命周期、span 创建/嵌套/异常捕获

## 2. LangGraph 节点集成

- [ ] 2.1 在 `workflow/graph.py` 中通过模块级 `_trace_manager` 变量注入 TraceManager（与 `_memory_manager` 模式一致）
- [ ] 2.2 在 `build_ticket_graph()` 中初始化 trace（start_trace），在 complete/handle_failure 节点中完成 trace（finish_trace）
- [ ] 2.3 为 receive、classify、process、review、notify、complete、handle_failure、auto_reply、escalate 节点包裹 `async with tracer.start_span()` 记录输入输出
- [ ] 2.4 编写 `tests/workflow/test_trace_integration.py`，测试节点 span 是否正确创建

## 3. ReAct 循环与 LLM 调用集成

- [ ] 3.1 在 `processor_react.py` 的 ReAct 循环中，为每轮迭代创建 `react_iter` span（parent 指向 process node span）
- [ ] 3.2 在每轮迭代中为 LLM 调用创建 `llm_call` span，记录 model、tokens
- [ ] 3.3 在工具调用时创建 `tool_call` span，记录 tool_name、params、result
- [ ] 3.4 在 `cached_client.py` 的 `chat_completions_create` 中集成 trace 记录（通过全局 TraceManager 或 context variable）
- [ ] 3.5 编写 `tests/agents/test_trace_react.py`，测试 ReAct 循环的 span 嵌套结构

## 4. REST API 与 WebSocket 集成

- [ ] 4.1 在 `api/routes.py` 中新增 `GET /api/tickets/{ticket_id}/trace` 端点，返回 trace 及 span 树（利用 parent_span_id 构建嵌套结构）
- [ ] 4.2 在 `api/routes.py` 中新增 `GET /api/traces` 端点，支持 status 过滤和分页
- [ ] 4.3 在 `api/routes.py` 中新增 `GET /api/traces/{trace_id}/stats` 端点，返回按 span_type 聚合的耗时统计
- [ ] 4.4 在 `api/routes.py` 的 `_broadcast_ticket_update()` 中增加 span_complete 事件推送
- [ ] 4.5 编写 `tests/api/test_trace_api.py`，测试 trace 查询、列表、统计端点

## 5. 应用集成与测试

- [ ] 5.1 在 `app.py` 的 lifespan 中初始化 TraceManager 并存入 app.state
- [ ] 5.2 在 `core/__init__.py` 中导出 TraceManager
- [ ] 5.3 运行全量测试，修复回归
- [ ] 5.4 部署到 HomeUbuntu 验证 trace 端点可用
