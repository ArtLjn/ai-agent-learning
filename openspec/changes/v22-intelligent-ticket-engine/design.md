## Context

智能算法与关键技术是 v2.2 架构的后台处理引擎。老师反馈要求算法模块不能只罗列 RAG、Trace、多 Agent，而要从工单业务流程解释输入、输出、算法方法和业务价值。本提案把智能处理拆为工单理解分类、知识检索增强、智能体协同、流程编排四类能力。

## Goals / Non-Goals

**Goals:**

- 将员工自然语言服务请求结构化为可处理工单字段。
- 根据分类、优先级、风险和字段完整性进行业务路由。
- 通过 rag-service 完成混合检索、重排和不可用降级。
- 通过 ReActProcessorAgent、ReviewerAgent、CoordinatorAgent 完成方案生成、质量评分和人工接管建议。
- 通过 LangGraph 管理自动处理、重试、人工审核、员工补充和完成归档。

**Non-Goals:**

- 不训练自有模型，不做模型微调。
- 不引入知识图谱或 GraphRAG。
- 不让智能引擎绕过人工审核自动执行真实退款、权限变更或外部系统写操作。

## Decisions

1. **工单理解和分类分两层。**  
   TicketIntentAgent 负责结构化字段，ClassifierAgent 负责类别、优先级、风险和路由解释，规则兜底保证 LLM 不可用时仍有基础分类。

2. **RAG 通过 rag-service 统一承载。**  
   主系统只通过 RAG Client 调用 retrieve/rerank/health，不直接访问 Qdrant。rag-service 不可用时返回 references=[] 并继续处理。

3. **质量审核是自动闭环的硬门槛。**  
   ReviewerAgent 低于阈值时触发重试，重试超限后转人工审核，避免低质量答案直接返回员工。

4. **状态机是人机协同边界。**  
   LangGraph 控制投诉、P0、高风险、字段缺失、审核失败、员工不满意等场景进入服务台处理端。

## Risks / Trade-offs

- **LLM 输出不稳定** -> JSON schema 校验、默认值、关键词规则兜底和 ReviewerAgent 二次审核。
- **RAG 不可用影响方案质量** -> 明确降级为无知识增强分支，Trace 记录 rag_service_reachable=false。
- **自动处理越权执行业务操作** -> 智能引擎只生成建议和状态流转，真实高风险业务进入人工审核。
