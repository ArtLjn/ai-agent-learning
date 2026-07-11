# HTTP API 接口协议

> 版本：v2.2
> 日期：2026-07-10
> 状态：v2.2 对齐企业内部服务台四分区架构；移除 per-user 配额接口口径，Token 仅保留系统级统计接口
> 关联设计：[业务架构梳理](../00_预设计/04_业务架构梳理.md) · [11_RAG服务独立项目设计.md](../01_正式设计/11_RAG服务独立项目设计.md) · [12_Token成本控制台设计.md](../01_正式设计/12_Token成本控制台设计.md) · [13_开发人员工作台设计.md](../01_正式设计/13_开发人员工作台设计.md)

## 0. v2.0 变更摘要

| 变更类型 | 内容 |
| --- | --- |
| 新增独立项目 | `rag-service`（端口 8001），提供 `/parse` `/ingest` `/retrieve` `/rerank` `/collections/{name}/documents` `/health` |
| 主系统新增 API（系统运维管理端） | `/api/admin/traces/*`、`/api/admin/prompts/*`、`/api/admin/rag/debug`、`/api/admin/stats/tokens*` |
| 主系统新增 API（服务台处理端 / 系统运维管理端） | `/api/admin/users`、`PATCH /api/admin/users/{user_id}`、`/api/admin/stats/adoption` |
| 通信契约 | 主系统通过 `tools/rag_client.py`（待建）调用 rag-service，详见 [03_Agent内部数据契约.md](./03_Agent内部数据契约.md) 第 10 章 |

> v1.x 的 `/api/tickets`、`/api/reviews`、`/api/knowledge` 等接口保持不变，本章新增内容追加在原章节之后。

## 1. 基本约定

后端业务接口统一以 `/api` 为前缀。请求和响应默认使用 JSON。接口实现位置为 `src/multi_agent_system/api/routes.py`，鉴权接口位于 `src/multi_agent_system/api/auth_routes.py`。

业务路由默认要求登录；当配置项 `auth_enabled=false` 时，`require_login` 会自动放行，便于本地演示。

返回体统一结构：`{ "code": "OK" | "FAILED", "message": str, "data": any }`（rag-service 与主系统一致）。本章示例为突出字段含义，多数仅展示 `data` 部分。

## 2. 鉴权接口

### 2.1 登录

`POST /api/auth/login`

请求体：

```json
{
  "username": "admin",
  "password": "password"
}
```

响应：

```json
{
  "username": "admin",
  "logged_in": true
}
```

说明：登录成功后后端写入 `agentdesk_session` cookie。密码使用 bcrypt 哈希校验，明文密码不应进入仓库。

### 2.2 退出登录

`POST /api/auth/logout`

响应：

```json
{
  "logged_out": true
}
```

### 2.3 当前登录状态

`GET /api/auth/me`

响应：

```json
{
  "logged_in": true,
  "username": "admin",
  "auth_enabled": true
}
```

## 3. 工单接口

### 3.1 创建工单

`POST /api/tickets`

请求体：

```json
{
  "content": "无法登录系统，点击登录按钮后报错 500",
  "user_id": "U001"
}
```

响应：

```json
{
  "ticket_id": "TK-20260624-001",
  "status": "received"
}
```

说明：接口会先调用 `TicketIntentAgent` 理解自然语言工单，提取分类、优先级、影响范围等信息并格式化正文；随后立即返回，后台异步执行 LangGraph 工作流。

### 3.2 批量创建工单

`POST /api/tickets/batch`

请求体：

```json
{
  "tickets": [
    {
      "content": "系统报错",
      "user_id": "U001"
    },
    {
      "content": "我想咨询套餐价格",
      "user_id": "U002"
    }
  ]
}
```

响应：

```json
{
  "results": {
    "ticket_0": {
      "ticket_id": "TK-20260624-001",
      "status": "received"
    }
  }
}
```

### 3.3 查询工单详情

`GET /api/tickets/{ticket_id}`

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ticket_id` | string | 工单 ID |
| `content` | string | 工单内容 |
| `category` | string/null | 分类 |
| `priority` | string/null | 优先级 |
| `processing_result` | string/null | 处理结果 |
| `references` | array | 知识库引用列表 |
| `review_score` | number/null | 审核评分 |
| `retry_count` | number | 重试次数 |
| `status` | string | 当前状态 |
| `error` | string/null | 错误信息 |
| `created_at` | string | 创建时间 |

### 3.4 查询工单列表

`GET /api/tickets?status=&category=&limit=&offset=`

查询参数：

| 参数 | 说明 |
| --- | --- |
| `status` | 可选，按状态过滤 |
| `category` | 可选，按分类过滤 |
| `limit` | 返回数量，默认 20，最大 100 |
| `offset` | 分页偏移 |

### 3.5 提交反馈

`POST /api/tickets/{ticket_id}/feedback`

请求体：

```json
{
  "satisfied": true
}
```

响应：

```json
{
  "status": "ok",
  "ticket_id": "TK-20260624-001",
  "satisfied": true
}
```

说明：当 `satisfied=false` 且工单已完成时，系统会创建 `user_request` 类型人工审核单，并将工单状态转为 `pending_human_review`。

### 3.6 查询工单沟通记录

`GET /api/tickets/{ticket_id}/messages`

响应：

```json
[
  {
    "message_id": "TM-20260629-001",
    "ticket_id": "TK-20260629-001",
    "sender_type": "reviewer",
    "sender_id": "reviewer-001",
    "content": "请补充订单号和支付流水号",
    "metadata": {"source": "request_info"},
    "created_at": "2026-06-29T10:00:00"
  }
]
```

说明：该接口用于工单详情页展示审核员请求补充和用户回复。工单不存在时返回 404。

### 3.7 提交用户补充信息

`POST /api/tickets/{ticket_id}/messages`

请求体：

```json
{
  "content": "订单号是 202606290001，支付流水号是 PAY123456",
  "sender_id": "user-001"
}
```

响应：

```json
{
  "status": "ok",
  "ticket_id": "TK-20260629-001",
  "next_node": "complete",
  "workflow_resumed": true,
  "ticket_status": "completed"
}
```

行为：

1. 校验工单存在。
2. 校验工单状态必须为 `waiting_user_input`，否则返回 409。
3. 写入 `ticket_messages`，`sender_type=user`。
4. 调用 `resume_from_user_input`，读取沟通记录构造 `conversation_context`。
5. 从 `process` 节点恢复工作流，并通过 WebSocket 推送状态更新。

## 4. 知识库接口

### 4.1 查询知识库文档

`GET /api/knowledge?limit=&offset=`

响应：

```json
{
  "documents": [
    {
      "id": "doc-001",
      "title": "登录失败处理手册",
      "category": "technical",
      "source": null,
      "content": "完整内容",
      "preview": "内容预览",
      "chunk_count": 2,
      "chunks": [
        {
          "index": 0,
          "content": "分块内容",
          "point_id": "qdrant-point-id"
        }
      ]
    }
  ],
  "count": 1,
  "next_offset": null
}
```

说明：Qdrant 不可用时返回 503。

### 4.2 上传知识文档

`POST /api/knowledge`

请求体：

```json
{
  "title": "登录失败处理手册",
  "content": "当用户无法登录时，先检查账号状态和密码错误次数。",
  "category": "technical"
}
```

响应：

```json
{
  "status": "ok",
  "chunks_added": 1,
  "message": "文档已上传"
}
```

## 5. 设置与统计接口

### 5.1 获取系统设置摘要

`GET /api/settings`

响应包含 LLM、Embedding、Qdrant、缓存、重试、审核阈值、并发、模型路由和 API 端口等只读配置摘要。敏感字段只返回是否已配置，不返回完整密钥。

### 5.2 获取统计数据

`GET /api/analytics`

响应包含：

- `category_distribution`
- `priority_distribution`
- `resolution_stats`
- `daily_stats`
- `efficiency`
- `evaluation`

## 6. 执行追踪接口

### 6.1 查询工单 Trace

`GET /api/tickets/{ticket_id}/trace`

响应包含 trace 基本信息、工单摘要、分类、优先级、处理结果、引用数量和 `spans` 树。

### 6.2 查询 Trace 列表

`GET /api/traces?status=&limit=&offset=`

### 6.3 查询 Trace 统计

`GET /api/traces/{trace_id}/stats`

### 6.4 查询 Trace 决策点

`GET /api/traces/{trace_id}/decisions`

响应：

```json
{
  "trace_id": "tr-001",
  "decision_count": 2,
  "decisions": [
    {
      "span_id": "sp-001",
      "span_name": "classify",
      "span_type": "node",
      "decision_type": "routing",
      "trigger": {"content_preview": "..."},
      "options_count": 4,
      "options": [],
      "selection_value": "technical",
      "confidence": 0.92,
      "reason": "登录失败属于技术问题",
      "start_time": 1710000000.0,
      "duration": 0.8
    }
  ]
}
```

说明：该接口从 `spans.metadata.decision` 中提取分类、审核、重试边界等决策语义。

## 7. 人工审核接口

详细设计参见 [01_正式设计/09_人工审核工作台设计.md](../01_正式设计/09_人工审核工作台设计.md)。

### 7.1 查询待审核队列

`GET /api/reviews/queue`

查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `trigger_type` | string | 否 | `escalate` / `review_failed` / `error_fallback` / `user_request` |
| `category` | string | 否 | 工单分类 |
| `priority` | string | 否 | 工单优先级 |
| `limit` | int | 否 | 默认 20，上限 100 |
| `offset` | int | 否 | 默认 0 |

响应：

```json
{
  "queue": [
    {
      "review_id": "HR-TK-20260627-001",
      "ticket_id": "TK-20260627-001",
      "trigger_type": "escalate",
      "trigger_reason": "投诉类工单",
      "content_preview": "我对昨天购买的...",
      "category": "complaint",
      "priority": "P1",
      "ai_suggestion": {
        "recommended_decision": "reprocess",
        "confidence": 0.72,
        "reasoning": "...",
        "key_concerns": ["..."]
      },
      "waiting_seconds": 1200,
      "created_at": "2026-06-27T10:00:00"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

### 7.2 查询审核详情

`GET /api/reviews/{ticket_id}`

返回该工单的完整审核上下文：原文、分类、优先级、AI 处理结果、trace 摘要、历史决策、当前 AI 建议。

### 7.3 提交审核决策

`POST /api/reviews/{ticket_id}/decision`

请求体：

```json
{
  "decision": "approve | reject | rewrite | reprocess | request_info",
  "decision_reason": "审核员填写的理由（必填）",
  "rewritten_result": "仅 decision=rewrite 时必填",
  "reviewer_id": "reviewer-001"
}
```

响应：

```json
{
  "status": "ok",
  "ticket_id": "TK-...",
  "next_node": "notify | process | complete | waiting_user_input",
  "workflow_resumed": true
}
```

行为：

- `approve`：沿用当前 `processing_result`，恢复到 `notify → complete`。
- `rewrite`：用 `rewritten_result` 覆盖处理结果，恢复到 `notify → complete`。
- `reprocess`：清空处理结果和重试次数，恢复到 `process`。
- `reject`：标记驳回并进入 `complete`。
- `request_info`：调用 `pause_for_user_input`，工单进入 `waiting_user_input`，写入一条审核员补充请求消息，本次不恢复自动工作流。

校验规则：

- 工单不存在返回 404。
- 工单状态不是 `pending_human_review` 返回 409。
- `decision_reason` 必填且不能为空白。
- `decision=rewrite` 时 `rewritten_result` 必填。
- `decision=request_info` 时 `decision_reason` 即展示给用户的补充说明。

错误码：

| HTTP | 错误码 | 场景 |
| --- | --- | --- |
| 404 | `detail` 文本 | 工单不存在 |
| 409 | `detail` 文本 | 工单不在待审核状态 |
| 422 | Pydantic 校验错误 | decision_reason 为空，或 rewrite 未提供 rewritten_result |

### 7.4 审核统计

`GET /api/reviews/stats`

```json
{
  "pending_count": 3,
  "decided_today": 12,
  "decision_distribution": {"approve": 7, "rewrite": 3, "reprocess": 1, "reject": 1, "request_info": 2},
  "avg_decision_seconds": 320,
  "ai_adoption_rate": 0.58
}
```

`ai_adoption_rate` 表示审核员最终决策与 AI 建议一致的比例，是论文的核心评估指标。

## 8. 健康检查与指标接口

这些接口由 `api/app.py` 注册，不带 `/api` 前缀。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 服务健康状态、缓存和模型路由摘要 |
| GET | `/metrics` | JSON 格式运行指标 |
| GET | `/prometheus` | Prometheus exposition 格式指标 |

## 9. rag-service HTTP API（v2.0 新增）

> rag-service 是 v2.0 抽离的**独立项目**，监听端口 `8001`，提供可复用的 PDF 解析、向量化、检索、重排能力。设计详见 [11_RAG服务独立项目设计.md](../01_正式设计/11_RAG服务独立项目设计.md)。
>
> 该服务**不**带 `/api` 前缀，路由直接挂在根路径。所有接口走 JSON；文件上传走 multipart；返回体统一 `{ "code": "OK" | "FAILED", "message": str, "data": any }`。
>
> 主系统通过 `src/multi_agent_system/tools/rag_client.py`（待建）调用本服务，通信契约见 [03_Agent内部数据契约.md](./03_Agent内部数据契约.md) 第 10 章。

### 9.1 POST /parse

仅做文档解析与分块，**不写入向量库**。用于调用方预览分块效果或调试分块策略。

请求字段：

| 字段 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `file` | multipart form | file | 二选一 | 原始文档（PDF 走文件上传） |
| `text` | multipart form / JSON | string | 二选一 | TXT / Markdown 可直接传文本 |
| `strategy` | JSON body | string | 否 | `semantic` \| `fixed` \| `structure_aware`，默认 `structure_aware` |
| `chunk_size` | JSON body | int | 否 | 仅 `fixed` 策略生效，默认 500 |
| `chunk_overlap` | JSON body | int | 否 | 默认 50 |

响应 `data`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `doc_id` | string | 文档内容指纹（MD5 前 8 位） |
| `chunks` | array | 分块列表，每项含 `content` `chunk_index` `metadata.page` `metadata.category` |
| `layout_summary` | object | 版面分析摘要（标题数、段落数、表格数、图片数） |

错误码：

| HTTP | 错误码 | 场景 |
| --- | --- | --- |
| 400 | `UNSUPPORTED_FORMAT` | 不支持的文档格式 |
| 422 | `PARSE_FAILED` | 解析过程异常 |
| 507 | `MODEL_UNAVAILABLE` | OCR / 版面模型加载失败 |

示例：

```bash
curl -X POST http://localhost:8001/parse \
  -F "file=@manual.pdf" \
  -F "strategy=structure_aware"
```

```json
{
  "code": "OK",
  "data": {
    "doc_id": "a1b2c3d4",
    "chunks": [
      {"content": "...", "chunk_index": 0, "metadata": {"page": 1, "category": "title"}}
    ],
    "layout_summary": {"titles": 8, "paragraphs": 42, "tables": 3, "figures": 2}
  }
}
```

### 9.2 POST /ingest

完整链路：解析 → 分块 → 向量化 → 写入 Qdrant。

请求字段：

| 字段 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `file` / `text` | form / JSON | file / string | 二选一 | 原始文档 |
| `collection` | JSON body | string | 是 | 目标 collection 名 |
| `strategy` | JSON body | string | 否 | 同 `/parse` |
| `metadata.source` | JSON body | string | 否 | 文档来源标识 |
| `metadata.category` | JSON body | string | 否 | 业务类别（如 `technical` / `policy`） |

响应 `data`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `doc_id` | string | 文档 ID |
| `chunk_count` | int | 实际写入分块数 |
| `collection` | string | 实际写入的 collection |

错误码：

| HTTP | 错误码 | 场景 |
| --- | --- | --- |
| 404 | `COLLECTION_NOT_FOUND` | 目标 collection 不存在 |
| 422 | `INGEST_FAILED` | 入库过程异常 |
| 503 | `QDRANT_UNAVAILABLE` | Qdrant 不可用 |

### 9.3 POST /retrieve

混合检索入口。默认 `hybrid` 模式（向量 + BM25 + RRF 融合）。

请求字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | string | 是 | 查询语句 |
| `collection` | string | 是 | 目标 collection |
| `mode` | string | 否 | `vector` \| `bm25` \| `hybrid`，默认 `hybrid` |
| `top_k` | int | 否 | 默认 10 |
| `filters` | object | 否 | 元数据过滤（如 `{"category": "technical"}`） |
| `use_hyde` | bool | 否 | 是否启用 HyDE 查询改写，默认 false |

响应 `data`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `results` | array | 每项含 `id` `content` `score` `doc_id` `chunk_index` `metadata` |
| `debug.query_vector_dim` | int | 调试模式时返回 |

错误码：

| HTTP | 错误码 | 场景 |
| --- | --- | --- |
| 400 | `INVALID_MODE` | mode 取值非法 |
| 503 | `QDRANT_UNAVAILABLE` | Qdrant 不可用 |

示例响应：

```json
{
  "code": "OK",
  "data": {
    "results": [
      {
        "id": "ch-01",
        "content": "登录失败排查步骤：1. 检查账号状态...",
        "score": 0.82,
        "doc_id": "a1b2c3d4",
        "chunk_index": 0,
        "metadata": {"source": "manual.pdf", "page": 1, "category": "technical"}
      }
    ]
  }
}
```

### 9.4 POST /rerank

Cross-Encoder 重排。通常与 `/retrieve` 串行使用：`/retrieve` 召回 top-20 → `/rerank` 取 top-5。

请求字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | string | 是 | 原始查询 |
| `documents` | array | 是 | 待重排文档列表（结构同 `/retrieve` 的 results） |
| `top_k` | int | 否 | 默认 5 |
| `model` | string | 否 | 默认 `BAAI/bge-reranker-v2-m3` |

响应 `data`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `results` | array | 重排后的文档，按相关性降序 |

错误码：

| HTTP | 错误码 | 场景 |
| --- | --- | --- |
| 507 | `RERANKER_MODEL_UNAVAILABLE` | Cross-Encoder 加载失败（自动降级为按原 score 排序并附 warning） |

### 9.5 GET /collections/{name}/documents

查询指定 collection 内的文档元数据列表。

请求字段：

| 字段 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `name` | path | string | 是 | collection 名 |
| `page` | query | int | 否 | 默认 1 |
| `page_size` | query | int | 否 | 默认 20 |

响应 `data`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `total` | int | 文档总数 |
| `documents` | array | 每项含 `doc_id` `chunk_count` `metadata` `ingested_at` |

### 9.6 GET /health

健康检查，无鉴权。

```json
{
  "status": "ok",
  "components": {
    "qdrant": "ok",
    "embedder": "ok",
    "reranker": "ok"
  }
}
```

任一关键组件不可用则 `status=degraded`。主系统 `rag_client.py` 在 `degraded` 状态下会跳过 `/rerank` 仅用 `/retrieve`，详见 [11_RAG服务独立项目设计.md](../01_正式设计/11_RAG服务独立项目设计.md) 第 11、13 章。

## 10. 主系统 v2.0 新增 HTTP API

> 以下接口均挂在主系统（端口 8000），统一 `/api/admin/*` 前缀，要求 admin 鉴权（与第 7 章人工审核接口同套 `require_admin`）。设计背景见 [13_开发人员工作台设计.md](../01_正式设计/13_开发人员工作台设计.md) 与 [12_Token成本控制台设计.md](../01_正式设计/12_Token成本控制台设计.md)。

### 10.1 系统运维管理端

#### 10.1.1 GET /api/admin/traces/{ticket_id}

获取工单的完整 trace（含 spans 树）。响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `trace_id` | string | trace ID |
| `ticket_id` | string | 工单 ID |
| `status` | string | trace 状态 |
| `total_tokens` | int | 累计 token（v1.1 P0 修复后才有真实值） |
| `spans` | array | span 树，每项含 `span_id` `parent_span_id` `span_type` `name` `duration` `metadata` |

`spans[].metadata` 内可能包含 `decision` / `token_usage` / `rag_stats` 三种子结构，schema 见 [03_Agent内部数据契约.md](./03_Agent内部数据契约.md) 第 11、12 章。

#### 10.1.2 GET /api/admin/traces/{ticket_id}/spans/{span_id}

获取单个 span 的详情，包括 `input_data` `output_data` `metadata_` 全量字段。主要用于 Trace 决策树的"展开节点详情"。

#### 10.1.3 GET /api/admin/prompts/{agent_name}/versions

Prompt 版本列表。`agent_name` 取值：`ticket_intent` / `classifier` / `react_processor` / `reviewer` / `coordinator`。

响应：

```json
{
  "agent_name": "classifier",
  "versions": [
    {
      "version": 3,
      "is_active": true,
      "template": "You are an expert classifier...",
      "note": "增加 few-shot 示例",
      "created_by": "dev-001",
      "created_at": "2026-07-01T10:00:00"
    }
  ]
}
```

#### 10.1.4 POST /api/admin/prompts/{agent_name}/versions

新建版本。请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `template` | string | 是 | Prompt 模板原文 |
| `note` | string | 否 | 版本说明（few-shot 改动、参数调整等） |

响应：返回新版本的 `version` 编号（同 agent_name 内自增）。校验规则：`agent_name` 必须属于 5 个 Agent 之一，否则 422。

#### 10.1.5 POST /api/admin/prompts/{agent_name}/versions/{version}/activate

激活指定版本。事务内完成：同 agent_name 下其他版本 `is_active=false`，目标版本 `is_active=true`。响应 `{ "status": "ok", "activated_version": 3 }`。

#### 10.1.6 POST /api/admin/rag/debug

RAG 检索调试器入口。**透传**到 rag-service，主系统侧不做检索逻辑。

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | string | 是 | 查询语句 |
| `mode` | string | 否 | `vector` / `bm25` / `hybrid`，默认 `hybrid` |
| `top_k` | int | 否 | 默认 10 |
| `rerank` | bool | 否 | 是否对结果做重排对比，默认 true |
| `collection` | string | 否 | 默认 `knowledge_base` |

响应（含重排前后对比与排名变化）：

```json
{
  "query": "登录失败如何排查",
  "mode": "hybrid",
  "retrieval_results": [
    {"chunk_id": "ch-01", "score": 0.82, "payload": {"doc_id": "...", "page": 1}}
  ],
  "rerank_results": [
    {"chunk_id": "ch-01", "score": 0.91, "rank_change": 2}
  ],
  "elapsed_ms": 245
}
```

rag-service 不可用时返回 503，主系统不缓存降级结果（调试场景必须暴露真实状态）。

#### 10.1.7 GET /api/admin/stats/tokens

近 N 天 Token 用量汇总，按 `model × call_type` 分组。查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `days` | int | 否 | 默认 7 |

响应：

```json
{
  "total_tokens": 128500,
  "estimated_cost_cny": 1.92,
  "by_model": [{"model": "glm-4.6", "tokens": 96000}, {"model": "glm-4.5-air", "tokens": 32500}],
  "by_call_type": [{"call_type": "process", "tokens": 78000}, {"call_type": "classify", "tokens": 18500}]
}
```

`call_type` 枚举：`intent` / `classify` / `process` / `review` / `coordinator` / `rag`，定义见 [03_Agent内部数据契约.md](./03_Agent内部数据契约.md) 第 12.3 节。

#### 10.1.8 GET /api/admin/stats/tokens/daily

指定日期明细。查询参数 `date`（YYYY-MM-DD，默认今天）。返回该日按小时桶聚合的 token 用量。

#### 10.1.9 GET /api/admin/stats/tokens/hourly

按小时热力图。数据源：从 `spans` 表取 `span_type='llm_call'` 的 span，按 `start_time` 小时分桶聚合 token。

查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `days` | int | 否 | 默认 7（最近 N 天 × 24 小时矩阵） |

### 10.2 服务台处理端 / 系统运维管理端

#### 10.2.1 GET /api/admin/users

用户列表。查询参数 `limit` / `offset` / `keyword` / `status`。响应每项含 `user_id` `username` `nickname` `status` `role` `created_at`。

#### 10.2.2 PATCH /api/admin/users/{user_id}

更新用户状态或角色。请求体：

```json
{
  "status": "banned",
  "role": "user"
}
```

响应返回更新后的用户对象。封禁用户后，该用户不能继续提交工单。

#### 10.2.3 GET /api/admin/stats/adoption

决策采纳率统计（论文核心评估指标）。查询参数 `days`（默认 7）。

```json
{
  "total_decisions": 48,
  "ai_adoption_rate": 0.58,
  "by_decision": {
    "approve": 18,
    "rewrite": 8,
    "reprocess": 6,
    "reject": 4,
    "request_info": 12
  },
  "ai_consistent": 28
}
```

`ai_adoption_rate` = 审核员最终决策与 CoordinatorAgent `recommended_decision` 一致的工单比例，按第 7.4 节同名指标扩展为时间序列。

## 11. 相关文档

- [01_正式设计/09_人工审核工作台设计.md](../01_正式设计/09_人工审核工作台设计.md) — 管理员模块（鉴权 `require_admin`、人工审核闭环）
- [01_正式设计/11_RAG服务独立项目设计.md](../01_正式设计/11_RAG服务独立项目设计.md) — rag-service 完整设计（v2.0 新增）
- [01_正式设计/12_Token成本控制台设计.md](../01_正式设计/12_Token成本控制台设计.md) — `/api/admin/stats/tokens*` 系统级 Token 成本统计（v2.1 口径）
- [01_正式设计/13_开发人员工作台设计.md](../01_正式设计/13_开发人员工作台设计.md) — 开发人员模块整体设计（v2.0 新增）
- [02_WebSocket实时推送协议.md](./02_WebSocket实时推送协议.md) — 工单状态实时推送
- [03_Agent内部数据契约.md](./03_Agent内部数据契约.md) — Agent 输出、RAG Client、决策点五元组、Token 累加契约
