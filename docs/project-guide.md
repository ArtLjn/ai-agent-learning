# ai-agent-learning 项目学习导读

## 项目概述

这是一个从 **AI Agent 学习项目** 整理而来的毕业设计项目。当前毕业设计主线聚焦于第 3 层：基于多智能体协同的智能工单处理系统。

历史学习产物已归档到 `archive/learning/`，包括 ReAct、Plan-Execute、Reflection、论文阅读助手和文档处理工作流等示例。主源码目录保留：

1. **多 Agent 工单系统**（`src/multi_agent_system/`）：基于 LangGraph 的工单处理服务

RAG 检索能力已拆分到独立 `rag-service` 项目，主系统通过 `src/multi_agent_system/tools/rag_client.py` 调用其 HTTP 接口。

本文档重点分析完整的工单处理系统。

---

## 目录结构

```
src/multi_agent_system/
├── api/
│   ├── app.py          # FastAPI 应用（生命周期 + 中间件 + 指标端点）
│   └── routes.py       # REST API + WebSocket 路由
├── agents/
│   ├── classifier.py   # 分类 Agent：LLM 分类 + 关键词降级
│   ├── processor.py    # 处理 Agent：知识库检索 + 生成方案
│   ├── reviewer.py     # 审核 Agent：质量评分 0-1
│   └── coordinator.py  # 协调 Agent：升级/失败处理/报告
├── core/               # 基础设施层
│   ├── retry.py        # @with_retry 重试装饰器
│   ├── fallback.py     # FallbackRegistry 降级注册表
│   ├── cache.py        # LLM 调用结果缓存
│   ├── cached_client.py# 带缓存 + 路由的 OpenAI 客户端
│   ├── model_router.py # 按任务类型选模型
│   ├── metrics.py      # Prometheus 指标
│   ├── agent_metrics.py# Agent 执行指标装饰器
│   ├── concurrent.py   # 异步并发工具
│   ├── logging.py      # trace_id 链路追踪
│   └── json_parser.py  # LLM JSON 响应解析
├── models/
│   ├── ticket.py       # 工单数据模型（Pydantic DTO）
│   ├── review.py       # 人工审核数据模型（Pydantic DTO）
│   ├── knowledge.py    # 知识库模型
│   ├── base.py         # SQLAlchemy ORM declarative base
│   └── db.py           # SQLAlchemy ORM 表定义（7 张表）
├── tools/
│   ├── db_query.py     # 数据库查询工具（封装 DatabaseManager）
│   ├── knowledge_search.py # Qdrant 向量检索
│   ├── notification.py # 通知工具
│   └── analytics.py    # 统计分析
├── workflow/
│   ├── graph.py        # LangGraph 状态机（核心编排）
│   └── state.py        # 状态类型定义
└── config.py           # 全局配置
```

---

## 请求处理流程

```
POST /api/tickets
    │
    ▼
┌─────────────┐    ┌──────────────────────────────────────────┐
│ routes.py   │───▶│ workflow/graph.py (LangGraph 状态机)      │
│ 参数校验     │    │                                          │
│ 创建工单     │    │ START → receive → classify → route ──┐   │
│ 触发后台任务 │    │    │                                  │   │
└─────────────┘    │    ▼                                  │   │
                   │  ┌────────────┐  ┌──────────┐        │   │
                   │  │ inquiry    │  │ complaint│        │   │
                   │  │ → auto_reply│  │ → escalate│       │   │
                   │  └────────────┘  └──────────┘        │   │
                   │    │                                  │   │
                   │    ▼                                  │   │
                   │ process → review ──┬─ score≥0.7 → notify │
                   │    ▲              └─ score<0.7 → retry   │
                   │    │                 retry<3 → process    │
                   │    │                 retry≥3 → handle_fail│
                   │    └─────────────────────────────────────┘
                   │                    ↓
                   │              complete → END
                   └──────────────────────────────────────────┘
                            │
                            ▼
                    WebSocket 实时推送状态
```

### 条件路由逻辑

- **inquiry** → `auto_reply`（直接自动回复）
- **complaint** 或 **P0 优先级** → `escalate`（升级人工）
- **其他** → `process`（正常处理 → review）
- **review score >= 0.7** → `notify`（通过）
- **review score < 0.7** → 重试（最多 3 次）→ 仍失败则 `handle_failure`

---

## 四大 Agent 职责

| Agent | 输入 | 输出 | 核心能力 |
|-------|------|------|----------|
| **Classifier** | 工单内容 | `{category, priority, reason}` | LLM 分类 + 关键词降级 |
| **Processor** | 内容、分类、优先级 | `{result, references}` | 知识库检索 + 方案生成 |
| **Reviewer** | 原始内容、处理结果 | `{score, feedback}` | 四维度质量评分 |
| **Coordinator** | 工单全量数据 | 升级/失败/报告 | 全局决策 |

---

## 关键设计模式

| 模式 | 应用 |
|------|------|
| **装饰器** | `@with_retry` 重试、`@track_agent_execution` 指标 |
| **注册表** | `FallbackRegistry` 降级函数管理 |
| **代理** | `CachedLLMClient` 透明缓存 + 路由 |
| **策略** | `ModelRouter` 按任务选模型 |
| **状态机** | LangGraph `StateGraph` 工单生命周期 |

---

## 阅读顺序建议

### 第一阶段：建立整体认知（30 分钟）
1. `config.py` — 系统配置
2. `models/ticket.py` — 数据模型
3. `workflow/state.py` — 状态定义
4. `workflow/graph.py` — 状态机全貌

### 第二阶段：深入 Agent（60 分钟）
5. `agents/classifier.py` — 最简单的 Agent
6. `agents/processor.py` — 知识库检索
7. `agents/reviewer.py` — 质量评分
8. `agents/coordinator.py` — 全局协调

### 第三阶段：基础设施（60 分钟）
9. `core/exceptions.py` → `core/retry.py` → `core/fallback.py`
10. `core/cache.py` + `core/cached_client.py`
11. `core/model_router.py`
12. `core/metrics.py` + `core/agent_metrics.py`

### 第四阶段：API 和工具（30 分钟）
13. `api/app.py` → `api/routes.py`
14. `tools/knowledge_search.py` → `tools/db_query.py`

---

## 技术栈

| 类别 | 技术 |
|------|------|
| LLM 接口 | OpenAI SDK（兼容 Ollama Cloud） |
| 工作流编排 | LangGraph |
| API 框架 | FastAPI + Uvicorn |
| 数据校验 | Pydantic |
| 关系数据库 | SQLAlchemy 2.0 async（生产 MySQL/腾讯云、测试 SQLite 内存） |
| 向量数据库 | Qdrant |
| 缓存 | cachetools (LRU + TTL) |
| 指标 | prometheus-client |
| 日志 | loguru |
| 容器化 | Docker + docker-compose |
