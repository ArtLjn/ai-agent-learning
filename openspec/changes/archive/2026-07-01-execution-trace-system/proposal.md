## Why

当前系统缺少执行过程的可观测性——工单经过 LangGraph 管道时，无法回溯"哪个 Agent 花了多久、LLM 调用了几次、工具返回了什么、降级发生在哪一步"。面试中这也是高频考点（OpenTelemetry / 分布式追踪思想）。需要一套轻量级 Trace 追踪系统，记录工单处理的完整执行链路。

## What Changes

- 新增 SQLite 表 `traces` 和 `spans`，采用 OpenTelemetry 风格的 Trace/Span 数据模型
- 新增 `TraceManager`，提供 trace 生命周期管理（创建/完成/失败）和 span 记录（支持嵌套父子关系）
- 在 LangGraph 各节点包裹 span 记录，追踪每个节点的输入状态、输出状态、耗时
- 在 ReAct 循环中记录每轮 Thought/Action/Observation，包括工具调用参数和结果
- 在 LLM 调用层（CachedLLMClient）拦截记录模型、token 数、耗时
- 新增 REST API：`GET /api/tickets/{id}/trace`（完整执行链路）、`GET /api/traces`（列表查询）
- WebSocket 推送增强：在现有 `/ws/tickets/{id}` 上增加 span 完成事件实时推送

## Capabilities

### New Capabilities
- `trace-storage`: SQLite 存储层——traces + spans 表结构、索引、CRUD 操作、聚合查询
- `trace-collection`: TraceManager 核心——trace 生命周期、span 创建与嵌套、与现有 MemoryManager/DatabaseManager 的集成
- `trace-api`: REST + WebSocket API——执行链路查询、列表过滤、实时 span 推送

### Modified Capabilities

（无既有 spec 需要修改）

## Impact

- **数据库**: 新增 2 张表（traces、spans），通过 DatabaseManager 管理
- **代码改动**: `graph.py`（节点包裹）、`processor_react.py`（ReAct 追踪）、`cached_client.py`（LLM 拦截）、`routes.py`（新 API）
- **新增文件**: `core/trace.py`（TraceManager）、`core/trace_models.py`（数据模型）
- **依赖**: 无新外部依赖
- **性能**: span 记录为纯 SQLite 写入（<1ms），不影响主流程延迟
