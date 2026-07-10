# Agent 内部数据契约

> 版本：v2.0
> 日期：2026-07-01
> 状态：v2.0 新增「RAG Client 数据契约」「决策点五元组数据契约」「Token 统计累加契约」三章，对齐双项目 + 4 大模块架构
> 关联设计：[11_RAG服务独立项目设计.md](../01_正式设计/11_RAG服务独立项目设计.md) · [12_Token成本控制台设计.md](../01_正式设计/12_Token成本控制台设计.md) · [13_开发人员工作台设计.md](../01_正式设计/13_开发人员工作台设计.md) · [06_可观测与执行追踪设计.md](../01_正式设计/06_可观测与执行追踪设计.md)

## 0. v2.0 变更摘要

| 章节 | 变更 |
| --- | --- |
| 第 1–9 章 | v1.x 内容保持不变（TicketState、各 Agent 输出、人工审核决策、消息链、用户补充记录） |
| 第 10 章（新增） | RAG Client 数据契约：主系统 `tools/rag_client.py` 与 rag-service 的 Pydantic schema、降级协议 |
| 第 11 章（新增） | 决策点五元组数据契约：`spans.metadata.decision` 子结构 schema 与各 Agent trigger 枚举 |
| 第 12 章（新增） | Token 统计累加契约：`TokenUsageAccumulator` 接口、`token_daily_stats` 写入字段、`call_type` 枚举 |

## 1. TicketState

`TicketState` 是 LangGraph 工作流中的共享状态。各节点通过读取和更新该状态完成协作。

核心字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ticket_id` | string | 工单 ID |
| `content` | string | 原始工单内容 |
| `category` | string/null | 分类结果 |
| `priority` | string/null | 优先级 |
| `processing_result` | string/null | 处理结果 |
| `references` | list | 知识库引用列表 |
| `review_score` | number/null | 审核评分 |
| `retry_count` | number | 重试次数 |
| `status` | string | 当前状态 |
| `messages` | list | 节点消息链 |
| `error` | string/null | 错误信息 |
| `user_context` | object | 用户上下文，可选 |
| `__trace_id__` | string/null | 执行追踪 ID |
| `trigger_type` | string/null | 人工审核触发类型 |
| `trigger_reason` | string/null | 人工审核触发原因 |
| `__human_decision__` | object/null | 人工审核恢复时注入的决策信息 |
| `conversation_context` | string/null | 用户补充信息与审核员沟通上下文 |

## 2. TicketIntentAgent 输出

```json
{
  "title": "后台 504 无法登录",
  "category": "technical",
  "priority": "P1",
  "impact": "部分用户受影响",
  "expectation": "请尽快定位并恢复服务",
  "contact": "ops@example.com",
  "occurred_at": "今天 上午 10:15",
  "intent_kind": "incident",
  "requires_business_operation": false,
  "required_fields": [],
  "can_auto_resolve": true,
  "risk_level": "medium",
  "requires_human_review": false,
  "risk_reason": "",
  "confidence": 0.86,
  "reason": "描述包含 504 和无法登录",
  "content": "【问题标题】后台 504 无法登录\n【问题类型】技术支持\n..."
}
```

约束：

- `category` 和 `priority` 必须落在系统枚举范围内。
- `content` 是后续 LangGraph 工作流消费的格式化正文。
- `requires_business_operation=true` 且 `required_fields` 非空时，后续路由更倾向进入人工审核，由审核员判断是否请求用户补充。
- LLM 不可用时允许使用本地规则兜底。

## 3. ClassifierAgent 输出

```json
{
  "category": "technical",
  "priority": "P1",
  "intent_kind": "incident",
  "requires_business_operation": false,
  "required_fields": [],
  "can_auto_resolve": true,
  "risk_level": "medium",
  "requires_human_review": false,
  "risk_reason": "",
  "confidence": 0.9,
  "reason": "用户反馈系统报错，影响登录"
}
```

约束：

- `category` 必须属于 `technical`、`billing`、`complaint`、`inquiry`。
- `priority` 必须属于 `P0`、`P1`、`P2`、`P3`。
- `required_fields` 用于表达业务操作缺失字段，例如 `order_id`、`payment_record`、`user_id`。
- `reason` 应简短说明分类依据。

## 4. ReActProcessorAgent 输出

```json
{
  "result": "建议先检查账号状态，再查看服务端登录接口日志。",
  "references": [
    {
      "title": "登录失败处理手册",
      "score": 0.82
    }
  ]
}
```

约束：

- `result` 不能为空。
- `references` 可以为空数组。
- 如果知识库不可用，允许只返回 `result`。

## 5. ReviewerAgent 输出

```json
{
  "score": 0.86,
  "feedback": "方案覆盖了账号状态和服务端日志检查，具备可执行性。"
}
```

约束：

- `score` 范围为 0 到 1。
- `feedback` 应说明通过或不通过的原因。

## 6. CoordinatorAgent 输出

升级处理示例：

```json
{
  "status": "escalated",
  "reason": "投诉类工单需要人工介入",
  "assignee": "manual_support"
}
```

失败处理示例：

```json
{
  "status": "failed",
  "reason": "连续多次审核未通过",
  "suggestion": "建议人工复核"
}
```

人工审核辅助决策示例：

```json
{
  "recommended_decision": "request_info",
  "confidence": 0.72,
  "reasoning": "工单涉及退款核查，但缺少订单号和支付流水号",
  "key_concerns": ["缺少 order_id", "缺少 payment_record"]
}
```

## 7. 人工审核决策输入

```json
{
  "decision": "request_info",
  "decision_reason": "请补充订单号和支付流水号",
  "rewritten_result": "请先确认账号状态，再检查登录接口日志和 504 时间段网关日志。",
  "reviewer_id": "reviewer-001"
}
```

约束：

- `decision` 只能是 `approve`、`reject`、`rewrite`、`reprocess`、`request_info`。
- `decision_reason` 必填且不能为空白。
- `decision=rewrite` 时 `rewritten_result` 必填。
- `decision=request_info` 时不需要 `rewritten_result`，`decision_reason` 会作为用户可见的补充说明写入 `ticket_messages`。

## 8. 消息链约定

每个节点可向 `messages` 追加一条记录：

```json
{
  "role": "classifier",
  "content": "分类结果: technical, 优先级: P1"
}
```

消息链主要用于调试、追踪和详情展示，不作为强一致业务数据。

## 9. 用户补充沟通记录

`ticket_messages` 是可持久化的业务沟通记录，用于保存审核员补充请求和用户回复。

创建用户消息请求：

```json
{
  "content": "订单号是 202606290001，支付流水号是 PAY123456",
  "sender_id": "user-001"
}
```

查询返回结构：

```json
{
  "message_id": "TM-20260629-001",
  "ticket_id": "TK-20260629-001",
  "sender_type": "user",
  "sender_id": "user-001",
  "content": "订单号是 202606290001，支付流水号是 PAY123456",
  "metadata": {"source": "user_input"},
  "created_at": "2026-06-29T10:35:00"
}
```

恢复工作流时，系统读取最近 20 条消息并拼接为：

```text
[reviewer] 请补充订单号和支付流水号
[user] 订单号是 202606290001，支付流水号是 PAY123456
```

该文本写入 `TicketState.conversation_context`，`process` 节点会把它追加到处理 Agent 输入中，要求 Agent 结合原始工单和补充信息处理。

## 10. RAG Client 数据契约（v2.0 新增）

> 本章定义主系统 `src/multi_agent_system/tools/rag_client.py`（待建）与 rag-service（端口 8001）之间的 HTTP 通信契约。rag-service API 路径与字段详见 [01_HTTP_API接口协议.md](./01_HTTP_API接口协议.md) 第 9 章，整体设计见 [11_RAG服务独立项目设计.md](../01_正式设计/11_RAG服务独立项目设计.md)。

### 10.1 RagRetrieveRequest

```python
from pydantic import BaseModel, Field
from typing import Optional, Literal

class RagRetrieveRequest(BaseModel):
    """主系统向 rag-service 发起的检索请求。"""

    query: str = Field(..., min_length=1, description="查询语句")
    collection: str = Field(default="knowledge_base", description="目标 collection")
    mode: Literal["vector", "bm25", "hybrid"] = Field(
        default="hybrid", description="检索模式"
    )
    top_k: int = Field(default=10, ge=1, le=50)
    filters: Optional[dict] = Field(
        default=None, description="元数据过滤，如 {'category': 'technical'}"
    )
    use_hyde: bool = Field(default=False, description="是否启用 HyDE 查询改写")

    model_config = {"extra": "forbid"}
```

### 10.2 RagChunk

```python
class RagChunk(BaseModel):
    """单条检索命中的 chunk。"""

    id: str                          # chunk 唯一 ID
    score: float                     # 相关性分数（hybrid 模式为 RRF 融合分）
    content: str                     # chunk 原文
    doc_id: str                      # 所属文档 ID
    chunk_index: int                 # 文档内分块序号
    metadata: dict = Field(
        default_factory=dict,
        description="含 source / page / category / heading_path",
    )
```

### 10.3 RagRetrieveResponse

```python
from typing import List

class RagRetrieveResponse(BaseModel):
    """rag-service /retrieve 的响应封装。"""

    results: List[RagChunk]
    debug: Optional[dict] = Field(
        default=None, description="调试信息（query_vector_dim 等），仅 debug 模式返回"
    )
```

### 10.4 RagRerankRequest / RagRerankResponse

```python
class RagRerankRequest(BaseModel):
    query: str
    documents: List[RagChunk]              # 待重排的候选，通常为 /retrieve 的 top-20
    top_k: int = Field(default=5, ge=1, le=20)
    model: str = Field(default="BAAI/bge-reranker-v2-m3")


class RagRerankResponse(BaseModel):
    results: List[RagChunk]                # 重排后的文档，按相关性降序
    warnings: List[str] = Field(default_factory=list)
    # 例如 ["reranker_model_unavailable: fallback to original score"]
```

### 10.5 RagServiceUnavailable 异常类

```python
class RagServiceUnavailable(Exception):
    """rag-service 不可用时抛出，由调用方捕获并触发降级。

    触发场景：
    - 连接超时（默认 10s）
    - 重试 1 次后仍失败
    - rag-service 返回 503 QDRANT_UNAVAILABLE
    """

    def __init__(self, reason: str, *, recoverable: bool = True):
        self.reason = reason
        self.recoverable = recoverable
        super().__init__(f"rag-service unavailable: {reason}")
```

### 10.6 调用约定

| 决策点 | 实现 |
| --- | --- |
| 超时 | 单次请求 10 秒（`HTTP_TIMEOUT`） |
| 重试 | 网络错误重试 1 次，间隔 500ms；5xx 不重试 |
| 鉴权 | 毕设范围不引入，留作 P2 |
| 缓存 | 同一 query 5 分钟内复用检索结果（默认关闭） |

### 10.7 降级协议

主系统**不**因 RAG 失败而中止工单处理。ReActProcessorAgent 在捕获 `RagServiceUnavailable` 后，按下表写入 span 元数据并走「无知识增强」分支：

```python
# tools/rag_client.py 伪代码
async def retrieve_with_fallback(req: RagRetrieveRequest, ctx) -> RagRetrieveResponse:
    try:
        return await _http_post("/retrieve", req)
    except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
        # 1. 写入降级标记到当前 span 的 metadata.rag_stats
        ctx.current_span.metadata["rag_stats"] = {
            "rag_service_reachable": False,
            "fallback_reason": str(e),
            "hit_count": 0,
            "top_score": 0.0,
            "mode": req.mode,
        }
        # 2. 抛出异常，由 ReActProcessorAgent 捕获并跳过 references 拼接
        raise RagServiceUnavailable(reason=str(e))
```

ReActProcessorAgent 侧：

```python
# agents/react_processor.py 伪代码
try:
    resp = await rag_client.retrieve_with_fallback(req, ctx)
    references = [_to_reference(c) for c in resp.results[:5]]
    ctx.current_span.metadata.setdefault("rag_stats", {}).update({
        "rag_service_reachable": True,
        "hit_count": len(resp.results),
        "top_score": resp.results[0].score if resp.results else 0.0,
        "mode": req.mode,
    })
except RagServiceUnavailable:
    references = []  # 无知识增强
```

降级触发条件与主系统行为见 [11_RAG服务独立项目设计.md](../01_正式设计/11_RAG服务独立项目设计.md) 第 13.3 节。

## 11. 决策点五元组数据契约（v2.0 新增）

> 本章定义 `spans.metadata.decision` 子结构 schema。决策语义是开发人员模块「Trace 决策树」与论文「可解释性」章节的核心数据。背景见 [13_开发人员工作台设计.md](../01_正式设计/13_开发人员工作台设计.md) 第 4 章。

### 11.1 DecisionMetadata

```python
from pydantic import BaseModel, Field
from typing import Optional, List

class DecisionOption(BaseModel):
    """候选选项。"""
    value: str
    score: float = Field(..., ge=0.0, le=1.0, description="该选项的评分/概率")
    reason: Optional[str] = Field(default=None, description="为何该选项得分如此")


class DecisionSelection(BaseModel):
    """最终选择。"""
    value: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str


class DecisionMetadata(BaseModel):
    """spans.metadata.decision 子结构。毕设范围 reflection 可省。"""

    trigger: str = Field(..., description="触发原因，见 11.2 trigger 枚举")
    options: List[DecisionOption] = Field(
        ..., description="候选选项列表，至少 2 项"
    )
    selection: DecisionSelection
    execution: dict = Field(
        ..., description="执行结果摘要，如 {'downstream_node': 'route'}"
    )
    reflection: Optional[dict] = Field(
        default=None,
        description="事后评估（毕设范围不实现，预留字段）",
    )
```

### 11.2 trigger 枚举（各 Agent）

| Agent / 节点 | trigger 取值 | 含义 | options 来源 |
| --- | --- | --- | --- |
| ClassifierAgent / `classify` 节点 | `classify` | 工单分类决策 | ClassifierAgent 输出的 all_options |
| TicketIntentAgent | `intent` | 工单意图理解 | 意图类别枚举（incident/inquiry/complaint/...） |
| `route` 节点（workflow） | `route_complaint` / `route_business_op` / `route_default` | 工单路由分支 | workflow 路由表 |
| `process`（ReAct）节点 | `tool_selection` | 每步工具选择 | ToolRegistry 候选 |
| ReviewerAgent / `review` 节点 | `quality_gate` | 审核通过/重试 | {pass, retry} |
| `retry_check` 节点 | `retry_boundary` | 是否到达重试上限 | {retry, escalate} |
| CoordinatorAgent / `human_review_wait` 节点 | `escalate` | 触发人工审核 | {approve, reject, rewrite, reprocess, request_info} |
| CoordinatorAgent（辅助决策） | `coordinator_suggest` | 为人工审核生成推荐决策 | 5 种 decision |

### 11.3 落库示例

```json
{
  "decision": {
    "trigger": "classify",
    "options": [
      {"value": "technical", "score": 0.82, "reason": "包含登录异常关键词"},
      {"value": "billing", "score": 0.15, "reason": "提及订单"},
      {"value": "complaint", "score": 0.03, "reason": "无情绪化表达"}
    ],
    "selection": {"value": "technical", "confidence": 0.82, "reason": "技术问题信号最强"},
    "execution": {"downstream_node": "route"}
  }
}
```

### 11.4 与 Trace 决策树的关系

Trace 决策树前端组件（`SpanTree` / `DecisionTimeline` / `DecisionCard`）直接消费该 schema：

- `trigger` 决定徽章颜色（routing 蓝、branching 紫、quality_gate 青、boundary 橙、tool_selection 灰、escalation 红）
- `selection.confidence` 决定色阶（< 0.5 红、[0.5, 0.7) 橙、[0.7, 0.9) 黄、≥ 0.9 绿）
- `options.length` 决定卡片左上角的"候选数"角标

埋点位置与 P0 修复清单见 [13_开发人员工作台设计.md](../01_正式设计/13_开发人员工作台设计.md) 第 4.2、11 节。

## 12. Token 统计累加契约（v2.0 新增）

> 本章定义各 Agent 在 LLM 调用后将 token 用量累加到 `token_daily_stats` 的统一约定。背景与 P0 bug 修复方案见 [12_Token成本控制台设计.md](../01_正式设计/12_Token成本控制台设计.md) 第 4.2 节。

### 12.1 TokenUsageAccumulator 接口

```python
from typing import Protocol, Optional

class TokenUsageAccumulator(Protocol):
    """所有 Agent 共享的 token 累加接口。

    实现位于 core/trace.py 的 TraceManager._accumulate_token_daily_stats，
    在每个 llm_call span 收尾时由 _finalize_span 调用。
    """

    async def accumulate(
        self,
        *,
        user_id: Optional[str],
        ticket_id: Optional[str],
        model: str,
        call_type: str,                # 见 12.3 枚举
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None: ...
```

### 12.2 写入字段约定

每次调用必须写入以下字段（来自 `token_daily_stats` 表，详见 [12_Token成本控制台设计.md](../01_正式设计/12_Token成本控制台设计.md) 第 3.1 节）：

| 字段 | 来源 | 备注 |
| --- | --- | --- |
| `user_id` | `span.context["user_id"]` | nullable：系统级调用（CoordinatorAgent）为 NULL |
| `ticket_id` | `span.context["ticket_id"]` | nullable：调试场景可空 |
| `model` | `span.attrs["model"]` | 如 `glm-4.6` / `glm-4.5-air` |
| `call_type` | `span.attrs["call_type"]` | 见 12.3 枚举；缺失则默认 `process` |
| `prompt_tokens` | `llm_result.usage.prompt_tokens` | int |
| `completion_tokens` | `llm_result.usage.completion_tokens` | int |
| `total_tokens` | 两项之和 | 由累加器计算 |
| `request_count` | 每次累加 +1 | UPSERT 维护 |
| `estimated_cost_cny` | `total_tokens × model_unit_price` | 单价表配置在 `config.yaml` |

UPSERT SQL（MySQL `ON DUPLICATE KEY UPDATE`）：

```sql
INSERT INTO token_daily_stats
    (user_id, date, model, call_type, ticket_id,
     prompt_tokens, completion_tokens, total_tokens, request_count)
VALUES (?, CURDATE(), ?, ?, ?, ?, ?, ?, 1)
ON DUPLICATE KEY UPDATE
    prompt_tokens     = prompt_tokens     + VALUES(prompt_tokens),
    completion_tokens = completion_tokens + VALUES(completion_tokens),
    total_tokens      = total_tokens      + VALUES(total_tokens),
    request_count     = request_count     + 1;
```

### 12.3 call_type 枚举

| call_type | 含义 | 来源 Agent | 典型 model |
| --- | --- | --- | --- |
| `intent` | 工单意图理解 | TicketIntentAgent | glm-4.5-air |
| `classify` | 分类与风险识别 | ClassifierAgent | glm-4.5-air |
| `process` | ReAct 处理 | ReActProcessorAgent | glm-4.6 |
| `review` | 质量审核 | ReviewerAgent | glm-4.5-air |
| `coordinator` | 协调与辅助决策 | CoordinatorAgent | glm-4.5-air |
| `rag` | RAG 检索调用（HyDE 改写） | rag-service 调用入口 | embedding-3 + glm-4.5-air |

> 约束：Agent 在调用 LLM 前必须把 `call_type` 写入 `span.attrs`，否则 `_finalize_span` 会按默认 `process` 归类，导致 ReActProcessor 的成本被高估。

### 12.4 各 Agent 接入示例

```python
# agents/classifier.py 伪代码
async def classify(self, state: TicketState, ctx) -> dict:
    span = ctx.start_span("classify", "node", attrs={
        "call_type": "classify",     # ← 必填
        "model": self.model_name,
    })
    try:
        result = await self.llm.invoke(prompt)
        # _finalize_span 内部会自动调用 TokenUsageAccumulator.accumulate
        return result
    finally:
        ctx.finish_span(span)
```

rag-service 的 HyDE 改写会消耗一次 LLM 调用，该调用由 rag-service 内部计费（计入 rag-service 自己的统计），**不**回流到主系统 `token_daily_stats`。主系统侧 `call_type=rag` 仅统计 `/api/admin/rag/debug` 调试入口触发的 LLM 调用（如对检索结果做摘要）。

## 13. 相关文档

- [01_正式设计/06_可观测与执行追踪设计.md](../01_正式设计/06_可观测与执行追踪设计.md) — Trace/Span 基础模型
- [01_正式设计/11_RAG服务独立项目设计.md](../01_正式设计/11_RAG服务独立项目设计.md) — rag-service 完整设计与降级链路（v2.0 新增）
- [01_正式设计/12_Token成本控制台设计.md](../01_正式设计/12_Token成本控制台设计.md) — Token 累加 P0 修复与系统级成本统计
- [01_正式设计/13_开发人员工作台设计.md](../01_正式设计/13_开发人员工作台设计.md) — 决策点五元组的消费方（v2.0 新增）
- [01_HTTP_API接口协议.md](./01_HTTP_API接口协议.md) — rag-service API、开发人员/管理员 API（v2.0 新增第 9、10 章）
- [02_WebSocket实时推送协议.md](./02_WebSocket实时推送协议.md) — 工单状态实时推送
