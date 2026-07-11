## Why

v2.2 定稿要求智能算法模块不能只写 RAG、Trace、多 Agent 名词，而要说明它在工单业务中处理了什么输入、输出了什么结果。需要单独提出智能算法与关键技术实施包，把工单理解分类、知识检索增强、智能体协同和流程编排凝练成可实现、可测试、可论文说明的能力。

## What Changes

- 将智能处理引擎拆为四类关键技术：工单理解分类算法、知识检索增强算法、智能体协同算法、流程编排策略。
- 强化 TicketIntentAgent 和 ClassifierAgent 的结构化输出、分类优先级、风险规则和关键词兜底。
- 强化 RAG 调用链路：查询改写、向量召回、关键词召回、融合排序、交叉编码重排、不可用降级。
- 强化 ReActProcessorAgent、ReviewerAgent、CoordinatorAgent 的协同边界：方案生成、质量评分、升级接管建议。
- 强化 LangGraph 状态机：人工接管判断、返工重试控制、员工补充恢复、处理结果流转和 Trace 决策记录。

## Capabilities

### New Capabilities

- `intelligent-ticket-engine`: 面向企业内部服务台工单的理解分类、检索增强、智能体协同和流程编排能力。

### Modified Capabilities

无。

## Impact

- 后端：`src/multi_agent_system/agents/`、`workflow/graph.py`、`tools/rag_client.py`、`core/trace.py`。
- RAG 服务：`rag-service` 的 retrieve/rerank/health 端点及主系统降级策略。
- 数据：工单结构化字段、分类优先级、风险标签、references、review_score、trace/span 决策元数据。
- 测试：分类路由、RAG 降级、处理结果质量审核、重试边界、人工接管、员工补充恢复和 Trace 决策记录。
