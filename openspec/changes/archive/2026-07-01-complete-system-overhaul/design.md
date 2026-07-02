## Context

当前 `multi_agent_system` 是一个基于 LangGraph 的多 Agent 工单处理系统，包含四大 Agent（Classifier / Processor / Reviewer / Coordinator）和基础基础设施（重试、降级、缓存、监控）。

核心短板：
- ProcessorAgent 是单轮 LLM 调用，没有多步推理能力
- 没有记忆系统，工单结束后数据全部丢失
- 工具调用靠 prompt 约束，没有 Schema 校验
- 上下文窗口无管理，messages 只增不减
- 评估只有主观评分，没有客观指标

## Goals / Non-Goals

**Goals:**
- ProcessorAgent 具备 ReAct 式多步推理和工具调用能力
- 工具调用有 Schema 约束和参数校验，参数错误可反馈修正
- 建立四层记忆体系（工作/短期/长期/语义），支持故障恢复
- 上下文窗口可管理，支持滑动窗口和摘要压缩
- Agent 评估覆盖主观评分 + 客观指标 + 用户反馈
- 数据持久化从内存迁移到 SQLite

**Non-Goals:**
- 不引入 Redis（单实例场景不需要）
- 不改造 ClassifierAgent 和 ReviewerAgent（它们适合结构化输出模式）
- 不实现多实例分布式部署
- 不替换 Qdrant 向量数据库
- 不做前端 UI 改造

## Decisions

### Decision 1: ProcessorAgent 用 ReAct 而非 Plan-Execute

**选择**：ProcessorAgent 升级为 ReAct 模式（Thought-Action-Observation 循环）。

**理由**：工单处理需要的步骤不固定。有的工单查一次知识库就能解决，有的需要查用户历史 + 查知识库 + 查用户信息。ReAct 让模型自己决定需要什么信息。Plan-Execute 适合步骤明确的任务，不适合这种开放式推理。

**替代方案**：Plan-Execute —— 需要预定义步骤模板，不够灵活。

### Decision 2: 工具 Schema 用 Pydantic 模型 + JSON Schema 导出

**选择**：每个工具定义 Pydantic 模型描述参数，运行时导出 JSON Schema 给 LLM，模型输出经 Pydantic 校验。

**理由**：
- Pydantic 提供类型校验、默认值、范围约束
- 可以复用 `model_json_schema()` 生成 OpenAI function calling 格式
- 校验失败时错误信息可直接反馈给模型修正

**替代方案**：手写 JSON Schema —— 容易出错，没有类型检查。

### Decision 3: 短期记忆用内存 + SQLite Checkpoint，不用 Redis

**选择**：短期记忆保存在内存（TicketState），每节点完成后写入 SQLite Checkpoint 表用于故障恢复。

**理由**：
- 单个工单处理在秒级到分钟级完成，生命周期内不需要跨进程共享
- SQLite 是 Python 内置，零部署成本
- Redis 增加容器依赖，学习项目不需要

**替代方案**：Redis —— 适合多实例共享会话，当前单实例场景过度设计。

### Decision 4: 长期记忆用 SQLite 而非 PostgreSQL

**选择**：用户画像、历史工单、常见模式存在 SQLite。

**理由**：
- 数据量预期在万级以下，SQLite 完全够用
- 单文件部署，备份简单
- 已有 `aiosqlite` 支持异步操作

**替代方案**：PostgreSQL —— 需要额外容器，学习项目不需要。

### Decision 5: 上下文压缩用"滑动窗口 + 摘要"两层策略

**选择**：messages 超过阈值时，先滑动窗口保留最近 N 轮；如果还超长，用 LLM 把丢弃的部分压缩成摘要。

**理由**：
- 滑动窗口简单高效，保留最近信息（通常最相关）
- 摘要补偿丢失的历史上下文
- 比纯截断信息损失小，比全量摘要计算成本低

**替代方案**：每轮都摘要 —— Token 消耗太大。

### Decision 6: DBQueryTool 直接扩展为 SQLite 存储，不新建 Repository 层

**选择**：把 DBQueryTool 的内存 dict 换成 SQLite 连接，在其上扩展长期记忆表。

**理由**：
- 保持现有接口不变，减少改动面
- DBQueryTool 已经是数据访问层，不需要再抽象 Repository
- 面试项目保持简洁，不过度分层

**替代方案**：新建 `repositories/` 包 —— 增加复杂度，收益不大。

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| ReAct 循环不稳定，模型不遵循格式 | 三层防护：JSON Schema 约束 + Pydantic 校验 + 错误反馈重试；同时保留降级到单轮生成的能力 |
| SQLite 写操作阻塞异步事件循环 | 使用 `aiosqlite` 异步驱动；Checkpoint 写操作放在节点完成后后台执行 |
| 上下文摘要增加 Token 消耗 | 摘要只在超限触发，不是每轮都执行；摘要长度限制在 200 tokens 以内 |
| 改造范围大，引入回归风险 | ProcessorAgent 保持 `process()` 接口不变，内部重构；所有改动有测试覆盖 |
| 长期记忆查询增加响应延迟 | 用户画像和历史工单查询加缓存；异步预加载 |

## Migration Plan

1. **Phase 1: SQLite 基础设施**
   - DBQueryTool 迁移到 SQLite
   - 创建 `tickets`、`users`、`checkpoints`、`patterns` 表
   - 保留内存模式作为测试回退

2. **Phase 2: 记忆系统**
   - TicketState 扩展记忆字段
   - 实现 Checkpoint 保存/恢复
   - LangGraph 节点集成记忆加载/保存

3. **Phase 3: ReAct Processor**
   - 实现 ToolBase 抽象和 Schema 定义
   - ProcessorAgent 重构为 ReAct 循环
   - 参数校验和错误反馈机制

4. **Phase 4: 上下文管理 + 评估**
   - 滑动窗口和摘要压缩
   - 评估框架和指标收集

5. **Rollback**: 保留原有 ProcessorAgent 实现为 `LegacyProcessorAgent`，配置切换即可回退。

## Open Questions

- 上下文摘要用哪个模型？和主模型相同，还是固定用轻量模型？
- 长期记忆的用户画像更新频率：每工单更新还是定时聚合？
- Checkpoint 保留时长：24小时是否足够？是否需要可配置？
