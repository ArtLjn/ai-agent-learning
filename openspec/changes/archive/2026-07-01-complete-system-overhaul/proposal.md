## Why

当前多 Agent 工单处理系统存在三个核心短板，制约其作为"企业级 Agent 系统"面试项目的可信度：

1. **Agent 缺乏真正的推理能力** — ProcessorAgent 是单轮 LLM 调用，没有 ReAct 式的多步推理和工具调用循环，无法处理需要查用户历史、查知识库、综合判断的复杂工单。
2. **没有记忆系统** — 工单结束后上下文全部丢失，没有用户画像、没有历史工单检索、没有跨会话的学习积累。
3. **工具调用不可靠** — 靠 prompt 约束模型输出格式，没有 Schema 校验和参数验证，模型容易瞎编参数。

这些问题是面试官追问 Agent 系统的必考点。补齐它们，项目才能经得起"工具调用怎么防幻觉""记忆怎么设计""上下文太长怎么办"等深度问题。

## What Changes

- **ProcessorAgent 升级为 ReAct 模式**：引入 Thought-Action-Observation 循环，支持多步推理和动态工具调用。
- **工具调用 Schema 化**：所有工具定义 JSON Schema，模型输出经 Pydantic 校验，参数错误时反馈修正。
- **分层记忆系统**：
  - 工作记忆（内存）：ReAct 循环内的推理状态和工具结果
  - 短期记忆（内存 + SQLite Checkpoint）：工单级上下文，支持故障恢复
  - 长期记忆（SQLite）：用户画像、历史工单、常见模式
  - 语义记忆（Qdrant，已有）：知识库向量检索
- **上下文窗口管理**：滑动窗口保留最近 N 轮，超限自动摘要压缩。
- **Agent 评估框架**：Reviewer 评分 + 客观指标（解决率/Token 消耗/耗时）+ 用户满意度反馈。
- **DBQueryTool 内存存储迁移到 SQLite**：为长期记忆提供持久化基础。

## Capabilities

### New Capabilities

- `agent-memory-system`: 分层记忆系统（工作记忆/短期记忆/长期记忆/语义记忆）的设计与实现
- `react-processor`: ProcessorAgent 的 ReAct 模式重构，含工具 Schema 约束和参数校验
- `context-management`: 上下文窗口管理（滑动窗口 + 摘要压缩 + 关键信息提取）
- `agent-evaluation`: Agent 评估框架（主观评分 + 客观指标 + 用户反馈）
- `sqlite-persistence`: SQLite 持久化层（替代内存存储，支撑 Checkpoint 和长期记忆）

### Modified Capabilities

- （无现有 spec，此项为空）

## Impact

- **核心 Agent**：ProcessorAgent 重构为 ReAct 模式，接口不变（`process()` 方法签名保持兼容）。
- **工具层**：新增 `ToolBase` 抽象基类，现有工具需适配 Schema 定义。
- **数据层**：DBQueryTool 从内存 dict 迁移到 SQLite，新增 `users`、`checkpoints`、`patterns` 表。
- **状态机**：TicketState 扩展记忆相关字段，LangGraph 节点函数需加载/保存记忆。
- **依赖**：新增 `pydantic`（已有）、`aiosqlite`（异步 SQLite）。
