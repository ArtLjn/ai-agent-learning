"""API 路由模块，提供工单 CRUD、知识库上传、统计查询和 WebSocket 实时推送。"""

import asyncio
import json
import random
from datetime import datetime
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from loguru import logger

from src.multi_agent_system.core import CachedLLMClient
from src.multi_agent_system.core.logging import generate_trace_id
from src.multi_agent_system.core.permissions import require_role
from src.multi_agent_system.models.message import TicketMessageCreate
from src.multi_agent_system.models.ticket import (
    BatchTicketCreate,
    TicketCategory,
    TicketCreate,
    TicketResponse,
)
from src.multi_agent_system.agents.ticket_intent import TicketIntentAgent
from src.multi_agent_system.models.review import ReviewDecisionRequest
from src.multi_agent_system.workflow.graph import (
    create_initial_state,
    resume_from_user_input,
)

__all__ = ["router"]

router = APIRouter()

# WebSocket 连接管理：ticket_id -> 订阅该工单的 WebSocket 列表
_ws_connections: dict[str, list[WebSocket]] = {}

# 全局 WebSocket 连接：接收所有工单状态更新
_global_ws_connections: list[WebSocket] = []

_MOCK_CATEGORY_GUIDANCE = {
    TicketCategory.INQUIRY.value: {
        "label": "咨询类",
        "instruction": "生成咨询类问题：用户主要询问入口、规则、操作方法或使用说明，语气平和。避免生成系统故障、P0、投诉或退款语气。",
    },
    TicketCategory.TECHNICAL.value: {
        "label": "技术类",
        "instruction": "生成技术类问题：用户遇到报错、登录失败、页面异常、接口失败或性能问题。除非明确大范围不可用，否则不要默认写成 P0 紧急事故。",
    },
    TicketCategory.BILLING.value: {
        "label": "账务类",
        "instruction": "生成账务类问题：用户围绕账单、扣费、退款、套餐、发票或支付记录求助，避免写成系统宕机。",
    },
    TicketCategory.COMPLAINT.value: {
        "label": "投诉类",
        "instruction": "生成投诉类问题：用户表达不满、服务体验差或要求反馈处理，但不要默认夸大成全站故障。",
    },
}


def _parse_references(ticket: dict[str, Any]) -> list[str]:
    """从数据库记录中解析知识库引用列表。"""
    raw = ticket.get("references_json")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _parse_trace_references(trace: dict[str, Any]) -> list[str]:
    """从 trace 关联的工单字段中解析知识库引用。"""
    raw = trace.get("ticket_references_json")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _enrich_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """补充 trace 的业务上下文字段，便于前端展示处理价值。"""
    enriched = dict(trace)
    references = _parse_trace_references(trace)
    enriched["ticket_summary"] = trace.get("ticket_content")
    enriched["ticket_category"] = trace.get("ticket_category")
    enriched["ticket_priority"] = trace.get("ticket_priority")
    enriched["ticket_result"] = trace.get("ticket_result")
    enriched["ticket_review_score"] = trace.get("ticket_review_score")
    enriched["reference_count"] = len(references)
    enriched["references"] = references
    enriched.pop("ticket_references_json", None)
    return enriched


def _pick_mock_question_document(
    docs: list[Any],
    category: str | None = None,
) -> dict[str, Any] | None:
    """从知识库文档中随机选择一个可用于出题的文档。"""
    candidates = [
        doc for doc in docs
        if isinstance(doc, dict) and _mock_doc_text(doc)
    ]
    if not candidates:
        return None
    if category:
        same_category = [
            doc for doc in candidates
            if str(doc.get("category") or "") == category
        ]
        if same_category:
            candidates = same_category
    return random.SystemRandom().choice(candidates)


def _mock_doc_text(doc: dict[str, Any]) -> str:
    """提取适合发给 LLM 的知识库片段文本。"""
    chunks = doc.get("chunks")
    chunk_text = ""
    if isinstance(chunks, list):
        chunk_text = "\n".join(
            str(chunk.get("content", ""))
            for chunk in chunks[:3]
            if isinstance(chunk, dict)
        )
    text = doc.get("preview") or doc.get("content") or chunk_text
    return str(text).strip()


async def _generate_mock_question_by_llm(
    doc: dict[str, Any],
    category: str | None = None,
) -> str:
    """调用 LLM 将知识库片段改写成自然用户问题。"""
    title = str(doc.get("title") or "知识库文档")
    doc_category = str(doc.get("category") or "未分类")
    requested_category = category or None
    guidance = _MOCK_CATEGORY_GUIDANCE.get(requested_category or "")
    category_label = guidance["label"] if guidance else doc_category
    category_instruction = guidance["instruction"] if guidance else "按知识库内容自然生成对应类型的问题。"
    content = _mock_doc_text(doc)[:1200]
    client = CachedLLMClient()
    response = await client.chat_completions_create(
        messages=[
            {
                "role": "system",
                "content": (
                    "你是工单演示数据生成 Agent。请根据给定知识库片段，"
                    "生成一条真实用户会提交的中文工单问题。要求："
                    "1. 不要复述知识库答案；2. 像用户遇到问题在求助；"
                    "3. 30 到 80 字；4. 不要编号，不要解释，只输出问题本身；"
                    "5. 可以自然包含焦虑、时间、影响范围或联系方式等细节；"
                    f"6. 本次必须生成{category_label}工单。{category_instruction}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"知识库标题：{title}\n"
                    f"知识库分类：{doc_category}\n"
                    f"目标生成类型：{category_label}\n"
                    f"知识库片段：\n{content}\n\n"
                    "请生成一条 mock 工单问题。"
                ),
            },
        ],
        temperature=0.95,
        cache=False,
        task_type="classify",
        max_tokens=120,
    )
    choice = response.choices[0] if getattr(response, "choices", None) else None
    message = getattr(choice, "message", None)
    raw = getattr(message, "content", "") if message is not None else ""
    return _clean_mock_question(str(raw))


def _clean_mock_question(value: str) -> str:
    """清理模型输出，避免带引号、列表符号或解释性前缀。"""
    text = value.strip().strip('"').strip("'").strip()
    text = text.removeprefix("问题：").removeprefix("工单问题：").strip()
    for prefix in ("-", "1.", "1、"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text[:120]


def _fallback_mock_question(
    doc: dict[str, Any] | None = None,
    category: str | None = None,
) -> str:
    """LLM 不可用时的随机兜底问题。"""
    if category == TicketCategory.INQUIRY.value:
        variants = [
            "我想咨询一下本月工单报表应该在哪里导出，需要提前配置权限吗？",
            "我想咨询这个功能的具体使用步骤，现在不太确定应该从哪个入口开始操作。",
            "我想咨询当前规则适用于哪些场景，需要准备哪些信息才能继续办理？",
        ]
        return random.SystemRandom().choice(variants)
    if category == TicketCategory.TECHNICAL.value:
        variants = [
            "我刚才打开页面时一直提示请求失败，刷新后还是不稳定，请帮我排查原因。",
            "登录后部分功能加载很慢，偶尔会报错，请帮我看下是不是配置异常。",
            "提交表单时页面没有成功反馈，我担心数据没保存，请帮我确认处理办法。",
        ]
        return random.SystemRandom().choice(variants)
    if category == TicketCategory.BILLING.value:
        variants = [
            "我想核对一下最近的扣费记录，账单金额和预期不一致，请帮我确认原因。",
            "付款后套餐状态还没更新，请帮我看下支付记录是否已经同步。",
            "我需要申请发票，但页面上找不到对应入口，请帮我说明办理步骤。",
        ]
        return random.SystemRandom().choice(variants)
    if category == TicketCategory.COMPLAINT.value:
        variants = [
            "我对这次处理结果不太满意，已经多次反馈还没有解决，请帮我升级跟进。",
            "客服回复没有解决我的问题，等待时间也比较长，请安排人员重新处理。",
            "这次服务体验不符合预期，我希望有人确认问题原因并给出明确答复。",
        ]
        return random.SystemRandom().choice(variants)
    if doc:
        title = str(doc.get("title") or "这个功能")
        variants = [
            f"我在处理“{title}”相关问题时卡住了，不确定下一步该怎么操作，请帮我看一下。",
            f"刚才遇到“{title}”相关异常，影响我继续使用，请帮我给出处理步骤。",
            f"我想咨询“{title}”这个场景，当前不知道需要准备哪些信息，请帮忙说明。",
        ]
    else:
        variants = [
            "我现在无法完成后台操作，页面一直没有明确提示，请帮我判断该怎么处理。",
            "我遇到一个业务流程问题，不确定需要提供哪些信息，请帮我整理处理步骤。",
            "刚才使用系统时流程中断了，影响继续操作，请帮我排查原因并给出方案。",
        ]
    return random.SystemRandom().choice(variants)


# ============================================================
# 工单接口
# ============================================================


@router.post("/tickets", response_model=dict)
async def create_ticket(body: TicketCreate, request: Request) -> dict:
    """提交新工单，由 Agent 理解用户意图后触发工作流。"""
    intent_agent = getattr(request.app.state, "ticket_intent_agent", None)
    if intent_agent is None:
        intent = TicketIntentAgent.extract_by_fallback(body.content)
    else:
        try:
            intent = await intent_agent.extract(body.content)
        except Exception as e:
            logger.warning(f"工单意图理解失败，使用本地规则兜底: {e}")
            intent = TicketIntentAgent.extract_by_fallback(body.content)

    state = create_initial_state(content=intent["content"])
    state["category"] = intent.get("category")
    state["priority"] = intent.get("priority")
    state["risk_level"] = intent.get("risk_level")
    state["requires_human_review"] = intent.get("requires_human_review")
    state["risk_reason"] = intent.get("risk_reason")
    ticket_id = state["ticket_id"]

    # 保存初始状态到数据库
    db_tool = request.app.state.db_tool
    ticket_data = {
        "ticket_id": ticket_id,
        "content": intent["content"],
        "user_id": body.user_id,
        "category": intent.get("category"),
        "priority": intent.get("priority"),
        "status": state["status"],
        "created_at": datetime.now().isoformat(),
    }
    await db_tool.save_ticket(ticket_data)

    # 后台异步执行工作流
    asyncio.create_task(
        _run_workflow(request.app, ticket_id, state)
    )

    logger.info(f"工单已创建: {ticket_id}")
    return {"ticket_id": ticket_id, "status": "received"}


@router.post("/tickets/batch", response_model=dict)
async def create_batch_tickets(body: BatchTicketCreate, request: Request) -> dict:
    """批量提交工单，使用 asyncio.gather 并发执行。

    每个工单独立初始化状态，通过 concurrent_execute 并发触发工作流，
    某个工单初始化失败不影响其他工单。
    """
    from src.multi_agent_system.core.concurrent import concurrent_execute

    settings = request.app.state.settings

    async def _init_one(ticket: TicketCreate) -> dict[str, str]:
        """初始化单个工单并触发后台工作流。"""
        state = create_initial_state(content=ticket.content)
        ticket_id = state["ticket_id"]

        # 保存初始状态
        db_tool = request.app.state.db_tool
        ticket_data = {
            "ticket_id": ticket_id,
            "content": ticket.content,
            "user_id": ticket.user_id,
            "status": state["status"],
            "created_at": datetime.now().isoformat(),
        }
        await db_tool.save_ticket(ticket_data)

        # 后台异步执行工作流
        asyncio.create_task(
            _run_workflow(request.app, ticket_id, state)
        )
        return {"ticket_id": ticket_id, "status": "received"}

    tasks = [
        (f"ticket_{i}", partial(_init_one, ticket))
        for i, ticket in enumerate(body.tickets)
    ]

    results = await concurrent_execute(
        tasks=tasks,
        max_concurrency=settings.max_concurrency,
    )

    logger.info(f"批量工单已提交: {len(body.tickets)} 个")
    return {"results": results}


@router.get("/tickets/mock-question", response_model=dict)
async def generate_mock_ticket_question(
    request: Request,
    category: TicketCategory | None = Query(
        default=None,
        description="指定 mock 问题生成类型",
    ),
) -> dict:
    """基于知识库随机片段生成一条自然用户口吻的 mock 工单问题。"""
    knowledge_tool = getattr(request.app.state, "knowledge_tool", None)
    requested_category = category.value if category else None

    try:
        docs_resp = knowledge_tool.list_documents(limit=100) if knowledge_tool else {}
        docs = docs_resp.get("documents", []) if isinstance(docs_resp, dict) else []
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"mock 工单读取知识库失败，使用兜底问题: {exc}")
        docs = []

    doc = _pick_mock_question_document(docs, requested_category)
    if doc is None:
        return {
            "prompt": _fallback_mock_question(category=requested_category),
            "generation_mode": "fallback",
            "knowledge_title": None,
            "category": requested_category,
        }

    try:
        prompt = await _generate_mock_question_by_llm(doc, requested_category)
        if prompt:
            return {
                "prompt": prompt,
                "generation_mode": "llm",
                "knowledge_title": doc.get("title"),
                "category": requested_category or doc.get("category"),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"mock 工单 LLM 生成失败，使用知识库随机兜底: {exc}")

    return {
        "prompt": _fallback_mock_question(doc, requested_category),
        "generation_mode": "fallback",
        "knowledge_title": doc.get("title"),
        "category": requested_category or doc.get("category"),
    }


@router.get("/tickets/{ticket_id}", response_model=TicketResponse)
async def get_ticket(ticket_id: str, request: Request) -> TicketResponse:
    """根据 ticket_id 查询工单详情。"""
    db_tool = request.app.state.db_tool
    ticket = await db_tool.get_ticket(ticket_id)

    if ticket is None:
        raise HTTPException(status_code=404, detail=f"工单 {ticket_id} 不存在")

    return TicketResponse(
        ticket_id=ticket.get("ticket_id", ticket_id),
        content=ticket.get("content", ""),
        category=ticket.get("category"),
        priority=ticket.get("priority"),
        processing_result=ticket.get("processing_result"),
        references=_parse_references(ticket),
        review_score=ticket.get("review_score"),
        retry_count=ticket.get("retry_count", 0),
        status=ticket.get("status", "received"),
        error=ticket.get("error"),
        created_at=ticket.get("created_at", datetime.now()),
    )


@router.get("/tickets/{ticket_id}/messages", response_model=list[dict])
async def list_ticket_messages(ticket_id: str, request: Request) -> list[dict]:
    """获取工单沟通记录。"""
    ticket = await request.app.state.db_manager.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"工单 {ticket_id} 不存在")
    return await request.app.state.db_manager.list_ticket_messages(ticket_id)


@router.post("/tickets/{ticket_id}/messages", response_model=dict)
async def create_user_ticket_message(
    ticket_id: str,
    body: TicketMessageCreate,
    request: Request,
) -> dict:
    """用户补充工单信息，并恢复后续处理。"""
    ticket = await request.app.state.db_manager.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"工单 {ticket_id} 不存在")
    if ticket.get("status") != "waiting_user_input":
        raise HTTPException(
            status_code=409,
            detail=(
                f"工单 {ticket_id} 当前不等待用户补充"
                f"（当前状态: {ticket.get('status')}）"
            ),
        )

    message_id = f"TM-{generate_trace_id()}"
    await request.app.state.db_manager.create_ticket_message({
        "message_id": message_id,
        "ticket_id": ticket_id,
        "sender_type": "user",
        "sender_id": body.sender_id,
        "content": body.content,
        "metadata": {"source": "user_input"},
    })
    trace_manager = getattr(request.app.state, "trace_manager", None)
    trace = await request.app.state.db_manager.get_trace_by_ticket(ticket_id)
    if trace_manager is not None and trace and trace.get("trace_id"):
        async with trace_manager.start_span(
            "ticket_message_created",
            "node",
            trace_id=trace["trace_id"],
            input_data={
                "ticket_id": ticket_id,
                "sender_type": "user",
                "content_length": len(body.content),
            },
        ) as span:
            span.set_output({"message_id": message_id})

    await _broadcast_ticket_update(
        ticket_id=ticket_id,
        status="waiting_user_input",
        message="用户已补充信息",
        node="ticket_message_created",
    )

    result = await resume_from_user_input(request.app, ticket_id)
    await _broadcast_ticket_update(
        ticket_id=ticket_id,
        status="processing",
        message="已收到补充信息，继续处理",
        node="workflow_resumed_from_user_input",
    )
    return result


@router.get("/tickets", response_model=list[TicketResponse])
async def list_tickets(
    request: Request,
    status: str | None = Query(default=None, description="按状态过滤"),
    category: str | None = Query(default=None, description="按分类过滤"),
    limit: int = Query(default=20, ge=1, le=100, description="返回数量"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
) -> list[TicketResponse]:
    """工单列表查询，支持状态和分类过滤以及分页。"""
    db_tool = request.app.state.db_tool
    tickets: list[dict[str, Any]] = await db_tool.list_tickets(
        status=status, category=category, limit=limit, offset=offset
    )

    return [
        TicketResponse(
            ticket_id=t.get("ticket_id", ""),
            content=t.get("content", ""),
            category=t.get("category"),
            priority=t.get("priority"),
            processing_result=t.get("processing_result"),
            references=_parse_references(t),
            review_score=t.get("review_score"),
            retry_count=t.get("retry_count", 0),
            status=t.get("status", "received"),
            error=t.get("error"),
            created_at=t.get("created_at", datetime.now()),
        )
        for t in tickets
    ]


# ============================================================
# 知识库接口
# ============================================================


@router.get("/knowledge", response_model=dict)
async def list_knowledge(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200, description="最多读取的分块数量"),
    offset: str | None = Query(default=None, description="Qdrant scroll 偏移量"),
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """查看知识库中已有文档列表和内容。"""
    knowledge_tool = request.app.state.knowledge_tool

    if knowledge_tool is None:
        raise HTTPException(
            status_code=503,
            detail="知识库服务不可用（Qdrant 未连接）",
        )

    try:
        return knowledge_tool.list_documents(limit=limit, offset=offset)
    except Exception as e:
        logger.error(f"知识库文档列表读取失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"知识库文档列表读取失败: {e}",
        ) from e


@router.post("/knowledge", response_model=dict)
async def upload_knowledge(
    body: dict[str, Any],
    request: Request,
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """上传文档到知识库。

    接收 JSON body，需包含 title、content，可选 category。
    """
    knowledge_tool = request.app.state.knowledge_tool

    if knowledge_tool is None:
        raise HTTPException(
            status_code=503,
            detail="知识库服务不可用（Qdrant 未连接）",
        )

    title = body.get("title", "")
    content = body.get("content", "")
    category = body.get("category")

    if not title or not content:
        raise HTTPException(
            status_code=400,
            detail="title 和 content 为必填字段",
        )

    document = {
        "title": title,
        "content": content,
        "category": category,
    }

    try:
        chunk_count = knowledge_tool.add_documents([document])
    except Exception as e:
        logger.error(f"知识库文档上传失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"知识库文档上传失败: {e}",
        ) from e

    return {
        "status": "ok",
        "chunks_added": chunk_count,
        "message": f"文档「{title}」已上传，共 {chunk_count} 个分块",
    }


# ============================================================
# 统计接口
# ============================================================


@router.get("/settings", response_model=dict)
async def get_system_settings(
    request: Request,
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """获取前端设置页展示用的只读配置摘要。"""
    settings = request.app.state.settings
    knowledge_tool = request.app.state.knowledge_tool

    return {
        "llm_base_url": settings.llm_base_url,
        "llm_api_key_configured": bool(settings.llm_api_key),
        "llm_model": settings.llm_model,
        "embedding_base_url": settings.embedding_base_url,
        "embedding_model": settings.embedding_model,
        "embedding_dim": settings.embedding_dim,
        "qdrant_url": settings.qdrant_url,
        "qdrant_collection": settings.qdrant_collection,
        "knowledge_available": knowledge_tool is not None,
        "cache_enabled": settings.cache_enabled,
        "cache_max_size": settings.cache_max_size,
        "cache_ttl": settings.cache_ttl,
        "max_retries": settings.max_retries,
        "review_threshold": settings.review_threshold,
        "review_timeout_threshold": settings.review_timeout_threshold,
        "ai_suggestion_high_confidence_threshold": settings.ai_suggestion_high_confidence_threshold,
        "max_messages": settings.context_max_messages,
        "checkpoint_ttl": settings.checkpoint_ttl,
        "max_react_iterations": settings.max_react_iterations,
        "max_concurrency": settings.max_concurrency,
        "model_routes": settings.model_routes,
        "fallback_model": settings.fallback_model,
        "api_host": settings.api_host,
        "api_port": settings.api_port,
    }


@router.post("/tickets/{ticket_id}/feedback", response_model=dict)
async def submit_feedback(
    ticket_id: str,
    body: dict[str, Any],
    request: Request,
) -> dict:
    """提交用户对工单处理结果的满意度反馈。

    satisfied=false 且工单已 completed 时，自动创建 user_request 类型 pending
    审核单，将工单转回人工审核队列。
    """
    from datetime import datetime

    from src.multi_agent_system.core.evaluation import EvaluationCollector
    from src.multi_agent_system.core.logging import generate_trace_id

    satisfied = body.get("satisfied", False)
    app = request.app
    db_manager = app.state.db_manager
    db_tool = app.state.db_tool
    collector = EvaluationCollector(db_manager=db_manager)

    try:
        await collector.record_user_feedback(ticket_id, satisfied)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # 不满意 + 工单已完成 -> 自动转人工审核
    if not satisfied:
        ticket = await db_tool.get_ticket(ticket_id)
        if ticket and ticket.get("status") == "completed":
            coordinator = getattr(app.state, "coordinator", None)
            ai_suggestion = None
            if coordinator is not None:
                try:
                    ai_suggestion = await coordinator.suggest_decision(
                        ticket_id,
                        "user_request",
                        "用户反馈不满意",
                        ticket.get("processing_result"),
                        ticket.get("review_score"),
                    )
                except Exception as ai_err:  # noqa: BLE001
                    logger.warning(f"feedback AI 建议失败: {ai_err}")

            review_id = f"HR-{generate_trace_id()}"
            await db_manager.create_pending_review({
                "review_id": review_id,
                "ticket_id": ticket_id,
                "trigger_type": "user_request",
                "trigger_reason": "用户反馈不满意",
                "ai_suggestion": ai_suggestion,
                "created_at": datetime.now().isoformat(),
            })

            await db_tool.save_ticket({
                **ticket,
                "ticket_id": ticket_id,
                "status": "pending_human_review",
            })

            await _broadcast_review_event(
                "review_requested",
                ticket_id,
                {
                    "trigger_type": "user_request",
                    "priority": ticket.get("priority"),
                    "review_id": review_id,
                },
            )

    return {"status": "ok", "ticket_id": ticket_id, "satisfied": satisfied}


# ============================================================
# 人工审核工作台接口
# ============================================================


_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _parse_ai_suggestion(raw: Any) -> dict | None:
    """解析 human_reviews.ai_suggestion 字段（JSON 字符串）。"""
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


@router.get("/reviews/queue", response_model=dict)
async def list_review_queue(
    request: Request,
    trigger_type: str | None = Query(default=None),
    category: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """查询待人工审核队列，按优先级 + 等待时长排序。"""
    db_manager = request.app.state.db_manager
    rows = await db_manager.list_pending_reviews_with_tickets(
        trigger_type=trigger_type,
        category=category,
        priority=priority,
        limit=limit,
        offset=offset,
    )
    total = await db_manager.count_pending_reviews(
        trigger_type=trigger_type,
        category=category,
        priority=priority,
    )

    now = datetime.now()
    queue: list[dict[str, Any]] = []
    for row in rows:
        created_at_str = row.get("created_at")
        try:
            created_at = datetime.fromisoformat(created_at_str) if created_at_str else now
        except ValueError:
            created_at = now
        waiting_seconds = int((now - created_at).total_seconds())
        content = row.get("ticket_content") or ""
        queue.append({
            "review_id": row.get("review_id"),
            "ticket_id": row.get("ticket_id"),
            "trigger_type": row.get("trigger_type"),
            "trigger_reason": row.get("trigger_reason"),
            "content_preview": content[:100],
            "category": row.get("ticket_category"),
            "priority": row.get("ticket_priority"),
            "ai_suggestion": _parse_ai_suggestion(row.get("ai_suggestion")),
            "waiting_seconds": waiting_seconds,
            "created_at": created_at_str,
        })

    # 兜底排序（DB 已排过，二次保险）
    queue.sort(
        key=lambda r: (
            _PRIORITY_ORDER.get(r.get("priority") or "", 9),
            -(r.get("waiting_seconds") or 0),
        )
    )

    return {"queue": queue, "total": total, "limit": limit, "offset": offset}


@router.get("/reviews/stats", response_model=dict)
async def get_review_stats_endpoint(
    request: Request,
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """返回审核工作台统计数据。"""
    db_manager = request.app.state.db_manager
    return await db_manager.get_review_workbench_stats()


@router.get("/reviews/{ticket_id}", response_model=dict)
async def get_review_detail(
    ticket_id: str,
    request: Request,
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """返回工单的完整审核上下文。"""
    db_tool = request.app.state.db_tool
    db_manager = request.app.state.db_manager

    ticket = await db_tool.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail=f"工单 {ticket_id} 不存在")

    reviews = await db_manager.list_reviews_by_ticket(ticket_id)
    current_review = next((r for r in reviews if r.get("status") == "pending"), None)
    history_reviews = [
        {
            "review_id": r.get("review_id"),
            "decision": r.get("decision"),
            "decided_at": r.get("decided_at"),
            "reviewer_id": r.get("reviewer_id"),
            "trigger_type": r.get("trigger_type"),
        }
        for r in reviews
        if r.get("status") == "decided"
    ]

    # trace 摘要
    trace_summary: dict[str, Any] | None = None
    trace = await db_manager.get_trace_by_ticket(ticket_id)
    if trace:
        trace_summary = {
            "trace_id": trace.get("trace_id"),
            "node_count": trace.get("node_count", 0),
            "duration": trace.get("duration"),
        }

    current_payload: dict[str, Any] | None = None
    if current_review:
        current_payload = {
            "review_id": current_review.get("review_id"),
            "trigger_type": current_review.get("trigger_type"),
            "trigger_reason": current_review.get("trigger_reason"),
            "ai_suggestion": _parse_ai_suggestion(current_review.get("ai_suggestion")),
            "created_at": current_review.get("created_at"),
        }

    return {
        "ticket_id": ticket_id,
        "content": ticket.get("content", ""),
        "category": ticket.get("category"),
        "priority": ticket.get("priority"),
        "status": ticket.get("status", "received"),
        "processing_result": ticket.get("processing_result"),
        "review_score": ticket.get("review_score"),
        "retry_count": ticket.get("retry_count", 0),
        "current_review": current_payload,
        "history_reviews": history_reviews,
        "trace_summary": trace_summary,
    }


@router.post("/reviews/{ticket_id}/decision", response_model=dict)
async def submit_review_decision(
    ticket_id: str,
    body: ReviewDecisionRequest,
    request: Request,
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """提交人工审核决策，恢复工作流执行。"""
    from src.multi_agent_system.workflow.graph import (
        pause_for_user_input,
        resume_from_human_decision,
    )

    app = request.app
    db_tool = app.state.db_tool

    # 1. 校验工单存在
    ticket = await db_tool.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail=f"工单 {ticket_id} 不存在")

    # 2. 校验工单状态
    if ticket.get("status") != "pending_human_review":
        raise HTTPException(
            status_code=409,
            detail=f"工单 {ticket_id} 不在待审核状态（当前状态: {ticket.get('status')}）",
        )

    # 3. 跨字段校验已由 ReviewDecisionRequest.@model_validator 完成
    #    FastAPI 在 Pydantic 校验失败时统一返回 422 + 结构化错误
    req = body

    # 4. 幂等性：已 decided 的工单不允许再次提交
    db_manager = app.state.db_manager
    pending = await db_manager.get_pending_review_by_ticket(ticket_id)
    if pending and pending.get("status") == "decided":
        raise HTTPException(
            status_code=409,
            detail=f"工单 {ticket_id} 的审核单已决策，请勿重复提交",
        )

    if req.decision.value == "request_info":
        result = await pause_for_user_input(
            app=app,
            ticket_id=ticket_id,
            decision_reason=req.decision_reason,
            reviewer_id=req.reviewer_id,
        )
        await _broadcast_review_event(
            "user_input_requested",
            ticket_id,
            {
                "reviewer_id": req.reviewer_id,
                "message": req.decision_reason,
            },
        )
        await _broadcast_ticket_update(
            ticket_id=ticket_id,
            status="waiting_user_input",
            message=req.decision_reason,
            node="request_info",
            data={"reviewer_id": req.reviewer_id},
        )
        return result

    # 6. 恢复工作流
    result = await resume_from_human_decision(
        app=app,
        ticket_id=ticket_id,
        decision=req.decision.value,
        decision_reason=req.decision_reason,
        rewritten_result=req.rewritten_result,
        reviewer_id=req.reviewer_id,
    )

    # 7. 广播 review_decided
    await _broadcast_review_event(
        "review_decided",
        ticket_id,
        {
            "decision": req.decision.value,
            "reviewer_id": req.reviewer_id,
            "next_node": result.get("next_node"),
        },
    )

    return {
        "status": "ok",
        "ticket_id": ticket_id,
        "next_node": result.get("next_node"),
        "workflow_resumed": True,
    }


@router.get("/analytics", response_model=dict)
async def get_analytics(request: Request) -> dict:
    """获取统计面板数据：分类分布 + 优先级分布 + 处理统计 + 评估指标。"""
    from src.multi_agent_system.core.evaluation import EvaluationCollector

    db_manager = request.app.state.db_manager
    analytics_tool = request.app.state.analytics_tool
    collector = EvaluationCollector(db_manager=db_manager)

    return {
        "category_distribution": await analytics_tool.get_category_distribution(),
        "priority_distribution": await analytics_tool.get_priority_distribution(),
        "resolution_stats": await analytics_tool.get_resolution_stats(),
        "daily_stats": await analytics_tool.get_daily_stats(),
        "efficiency": await collector.get_efficiency_stats(),
        "evaluation": await collector.get_evaluation_summary(),
    }


@router.get("/tickets/{ticket_id}/trace")
async def get_ticket_trace(ticket_id: str, request: Request) -> dict:
    """获取工单的完整执行 trace。"""
    db_manager = request.app.state.db_manager
    trace = await db_manager.get_trace_by_ticket(ticket_id)
    if trace is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Trace not found"})

    # 获取所有 span 并构建树
    spans = await db_manager.get_spans_by_trace(trace["trace_id"])
    span_tree = _build_span_tree(spans)

    return {
        "trace_id": trace["trace_id"],
        "ticket_id": trace["ticket_id"],
        "status": trace["status"],
        "duration": trace.get("duration"),
        "total_tokens": trace.get("total_tokens", 0),
        "total_tool_calls": trace.get("total_tool_calls", 0),
        "node_count": trace.get("node_count", 0),
        "start_time": trace.get("start_time"),
        "end_time": trace.get("end_time"),
        "ticket_summary": trace.get("ticket_content"),
        "ticket_category": trace.get("ticket_category"),
        "ticket_priority": trace.get("ticket_priority"),
        "ticket_result": trace.get("ticket_result"),
        "ticket_review_score": trace.get("ticket_review_score"),
        "reference_count": len(_parse_trace_references(trace)),
        "references": _parse_trace_references(trace),
        "spans": span_tree,
    }


@router.get("/traces")
async def list_traces(
    request: Request,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """查询 trace 列表。"""
    limit = min(limit, 100)
    db_manager = request.app.state.db_manager
    traces = await db_manager.list_traces(status=status, limit=limit, offset=offset)
    total = await db_manager.count_traces(status=status)
    return {
        "traces": [_enrich_trace(trace) for trace in traces],
        "count": len(traces),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/traces/{trace_id}/stats")
async def get_trace_stats(trace_id: str, request: Request) -> dict:
    """获取 trace 耗时分析。"""
    db_manager = request.app.state.db_manager
    stats = await db_manager.get_trace_stats(trace_id)
    if stats is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Trace not found"})
    return stats


@router.get("/traces/{trace_id}/decisions")
async def get_trace_decisions(trace_id: str, request: Request) -> dict:
    """列出 trace 内的所有决策点（从 spans.metadata.decision 提取）。"""
    db_manager = request.app.state.db_manager
    spans = await db_manager.get_spans_by_trace(trace_id)
    if not spans:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Trace not found"})

    decisions: list[dict[str, Any]] = []
    for span in spans:
        metadata = span.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = None
        if not isinstance(metadata, dict):
            continue
        decision = metadata.get("decision")
        if not isinstance(decision, dict):
            continue
        selection = decision.get("selection") or {}
        options = decision.get("options") or []
        decisions.append({
            "span_id": span.get("span_id"),
            "span_name": span.get("name"),
            "span_type": span.get("span_type"),
            "decision_type": decision.get("decision_type"),
            "trigger": decision.get("trigger"),
            "options_count": len(options),
            "options": options,
            "selection_value": selection.get("value"),
            "confidence": selection.get("confidence"),
            "reason": selection.get("reason"),
            "start_time": span.get("start_time"),
            "duration": span.get("duration"),
        })

    decisions.sort(key=lambda d: d.get("start_time") or 0)
    return {
        "trace_id": trace_id,
        "decision_count": len(decisions),
        "decisions": decisions,
    }


def _build_span_tree(spans: list[dict]) -> list[dict]:
    """将扁平 span 列表构建为嵌套树结构。"""
    span_map: dict[str, dict] = {}
    roots: list[dict] = []

    for span in spans:
        # 解析 JSON 字段
        for field in ("input_data", "output_data", "metadata"):
            val = span.get(field)
            if isinstance(val, str):
                try:
                    span[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        span["children"] = []
        span_map[span["span_id"]] = span

    for span in spans:
        parent_id = span.get("parent_span_id")
        if parent_id and parent_id in span_map:
            span_map[parent_id]["children"].append(span)
        else:
            roots.append(span)

    return roots


# ============================================================
# WebSocket 实时推送
# ============================================================


@router.websocket("/ws/tickets/{ticket_id}")
async def websocket_ticket_progress(websocket: WebSocket, ticket_id: str) -> None:
    """WebSocket 端点：客户端连接后实时接收工单处理进度更新。"""
    await websocket.accept()

    # 注册连接
    if ticket_id not in _ws_connections:
        _ws_connections[ticket_id] = []
    _ws_connections[ticket_id].append(websocket)

    try:
        # 保持连接，等待客户端断开
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.debug(f"WebSocket 客户端断开: ticket_id={ticket_id}")
    finally:
        _ws_connections[ticket_id].remove(websocket)
        if not _ws_connections[ticket_id]:
            del _ws_connections[ticket_id]


@router.websocket("/ws/monitor")
async def websocket_global_monitor(websocket: WebSocket) -> None:
    """全局 WebSocket 端点：接收所有工单的状态更新，用于前端实时刷新。"""
    await websocket.accept()
    _global_ws_connections.append(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.debug("全局 WebSocket 客户端断开")
    finally:
        if websocket in _global_ws_connections:
            _global_ws_connections.remove(websocket)


# 工作流节点名称到中文标签的映射
_NODE_LABELS: dict[str, str] = {
    "receive": "接收工单",
    "classify": "智能分类",
    "route": "路由决策",
    "process": "工单处理",
    "review": "质量审核",
    "auto_reply": "自动回复",
    "escalate": "升级处理",
    "notify": "发送通知",
    "complete": "归档完成",
    "retry_check": "重试检查",
    "request_user_input": "等待补充",
    "handle_failure": "失败处理",
}


async def _fallback_to_human_review(
    app: Any,
    ticket_id: str,
    state: dict,
    existing: dict,
    error_msg: str,
) -> None:
    """异常兜底：写入 error_fallback 类型人工审核单并更新工单状态。

    复用 human_review_wait 节点逻辑，但 trigger_type 固定为 error_fallback。
    """
    from datetime import datetime

    from src.multi_agent_system.core.logging import generate_trace_id

    db_manager = app.state.db_manager
    coordinator = getattr(app.state, "coordinator", None)

    # 更新工单状态为 pending_human_review
    await app.state.db_tool.save_ticket({
        **existing,
        "ticket_id": ticket_id,
        "status": "pending_human_review",
        "error": error_msg,
    })

    # 生成 AI 建议（可选，失败不阻塞）
    ai_suggestion = None
    if coordinator is not None:
        try:
            ai_suggestion = await coordinator.suggest_decision(
                ticket_id,
                "error_fallback",
                error_msg,
                state.get("processing_result"),
                state.get("review_score"),
            )
        except Exception as ai_err:  # noqa: BLE001
            logger.warning(f"error_fallback AI 建议失败: {ai_err}")

    # 写入 pending 审核单
    if db_manager is not None:
        await db_manager.create_pending_review({
            "review_id": f"HR-{generate_trace_id()}",
            "ticket_id": ticket_id,
            "trigger_type": "error_fallback",
            "trigger_reason": error_msg,
            "ai_suggestion": ai_suggestion,
            "created_at": datetime.now().isoformat(),
        })


async def _run_workflow(app: Any, ticket_id: str, state: dict) -> None:
    """后台执行工作流，每个节点完成后实时推送状态更新。"""
    workflow = app.state.workflow
    db_tool = app.state.db_tool
    current_state = dict(state)

    # 恢复 trace context：从 state 中取出 trace_id 并设置到 contextvar
    trace_id = state.get("__trace_id__")
    if trace_id:
        from src.multi_agent_system.core.trace import current_trace_id
        current_trace_id.set(trace_id)

    try:
        # 流式执行：每完成一个节点就推送一次更新
        async for event in workflow.astream(state):
            for node_name, node_output in event.items():
                if not isinstance(node_output, dict):
                    continue

                # 累积状态
                current_state.update(node_output)

                # 合并已有数据（保留 user_id、created_at 等字段）
                existing = await db_tool.get_ticket(ticket_id) or {}
                db_data = {
                    **existing,
                    "ticket_id": ticket_id,
                    "content": current_state.get("content", ""),
                    "category": current_state.get("category"),
                    "priority": current_state.get("priority"),
                    "processing_result": current_state.get("processing_result"),
                    "review_score": current_state.get("review_score"),
                    "retry_count": current_state.get("retry_count", 0),
                    "status": current_state.get("status", current_state.get("status", "processing")),
                    "error": current_state.get("error"),
                }
                if "references" in node_output:
                    db_data["references"] = node_output.get("references", [])
                await db_tool.save_ticket(db_data)

                status = db_data.get("status", "processing")
                label = _NODE_LABELS.get(node_name, node_name)
                logger.info(f"工单 {ticket_id} 节点 {node_name} 完成，状态: {status}")

                # 构建推送数据
                span_data = {
                    "category": db_data.get("category"),
                    "priority": db_data.get("priority"),
                    "review_score": db_data.get("review_score"),
                    "retry_count": db_data.get("retry_count", 0),
                }

                # 获取最近完成的 node span
                if hasattr(app.state, "trace_manager") and app.state.trace_manager:
                    db_mgr = app.state.db_manager
                    trace = await db_mgr.get_trace_by_ticket(ticket_id)
                    if trace:
                        recent_spans = await db_mgr.get_spans_by_trace(trace["trace_id"])
                        node_spans = [s for s in recent_spans if s["span_type"] == "node" and s.get("duration")]
                        if node_spans:
                            last_span = node_spans[-1]
                            span_data["span"] = {
                                "span_id": last_span["span_id"],
                                "span_type": last_span["span_type"],
                                "name": last_span["name"],
                                "duration": last_span["duration"],
                                "status": last_span["status"],
                            }

                # 推送节点完成事件
                await _broadcast_ticket_update(
                    ticket_id=ticket_id,
                    status=status,
                    message=f"{label} 完成",
                    node=node_name,
                    data=span_data,
                )
                node_delay = getattr(
                    getattr(app.state, "settings", None),
                    "workflow_node_delay_seconds",
                    0,
                )
                if node_delay > 0:
                    await asyncio.sleep(node_delay)

                # 人工审核请求事件：节点标记 __review_requested__ 时广播
                if node_output.get("__review_requested__"):
                    review_payload: dict[str, Any] = {
                        "event": "review_requested",
                        "ticket_id": ticket_id,
                        "status": status,
                        "trigger_type": node_output.get("trigger_type")
                        or current_state.get("trigger_type"),
                        "trigger_reason": node_output.get("trigger_reason")
                        or current_state.get("trigger_reason"),
                    }
                    pending_review = None
                    if hasattr(app.state, "db_manager") and app.state.db_manager:
                        pending_review = (
                            await app.state.db_manager.get_pending_review_by_ticket(
                                ticket_id
                            )
                        )
                    if pending_review:
                        review_payload["review_id"] = pending_review.get("review_id")
                        review_payload["ai_suggestion"] = pending_review.get(
                            "ai_suggestion"
                        )
                    await _broadcast_ticket_update(
                        ticket_id=ticket_id,
                        status=status,
                        message="工单已转入人工审核",
                        node="human_review_wait",
                        data=review_payload,
                    )
                    # 独立的 review_requested 事件（type 字段供前端按事件类型分发）
                    await _broadcast_review_event(
                        "review_requested",
                        ticket_id,
                        {
                            "trigger_type": review_payload.get("trigger_type"),
                            "trigger_reason": review_payload.get("trigger_reason"),
                            "priority": db_data.get("priority"),
                            "review_id": review_payload.get("review_id"),
                        },
                    )
                # 注：__review_decided__ marker 仅在 resume_from_human_decision
                # 子图中设置，本 _run_workflow 流处理器只跑初始流程不会看到该 marker。
                # review_decided 事件由 submit_review_decision 端点直接广播。

    except Exception as e:
        logger.error(f"工单处理异常: {ticket_id}, 错误: {e}")

        existing = await db_tool.get_ticket(ticket_id) or {}

        # 异常兜底：转为 error_fallback 类型人工审核，而非直接 failed
        # 三级兜底逻辑封装在 _safe_fallback_to_human_review 中，避免异常逸出
        await _safe_fallback_to_human_review(
            app, ticket_id, current_state, existing, str(e)
        )


async def _safe_fallback_to_human_review(
    app: Any,
    ticket_id: str,
    state: dict,
    existing: dict,
    error_msg: str,
) -> None:
    """三级兜底：确保异常不会从 _run_workflow（asyncio.create_task）静默逸出。

    层级：
    1. 调用 _fallback_to_human_review 转人工审核
    2. 第一层失败 -> 标记工单 failed + 广播
    3. 标记 failed 也失败 -> 仅记录 critical 日志（运维告警）
    """
    db_tool = app.state.db_tool
    fallback_err: Exception | None = None

    # 第一层：转人工审核
    try:
        await _fallback_to_human_review(
            app, ticket_id, state, existing, error_msg
        )
        await _broadcast_ticket_update(
            ticket_id=ticket_id,
            status="pending_human_review",
            message=f"处理异常，已转人工审核: {error_msg}",
            node="human_review_wait",
            data={"trigger_type": "error_fallback"},
        )
        return
    except Exception as err1:  # noqa: BLE001
        fallback_err = err1
        logger.error(
            f"工单 {ticket_id} 人工审核兜底也失败: {fallback_err}. "
            f"原始错误: {error_msg}"
        )

    # 第二层：标记 failed
    try:
        combined_error = f"原始错误: {error_msg}; 兜底失败: {fallback_err}"
        await db_tool.save_ticket({
            **existing,
            "ticket_id": ticket_id,
            "status": "failed",
            "error": combined_error,
        })
        await _broadcast_ticket_update(
            ticket_id=ticket_id,
            status="failed",
            message=f"处理失败且人工审核兜底也失败: {fallback_err}",
            node="error",
        )
        return
    except Exception as final_err:  # noqa: BLE001
        # 第三层：彻底失败，只记录日志（防止异常逸出 create_task 被静默吞掉）
        logger.critical(
            f"工单 {ticket_id} 完全无法处理: {final_err}. "
            f"原始: {error_msg}, 兜底: {fallback_err}"
        )


async def _broadcast_ticket_update(
    ticket_id: str,
    status: str,
    message: str,
    node: str | None = None,
    data: dict | None = None,
) -> None:
    """向所有 WebSocket 客户端广播状态更新（单工单订阅 + 全局监控）。"""
    now = datetime.now()
    payload = {
        "ticket_id": ticket_id,
        "status": status,
        "message": message,
        "timestamp": now.isoformat(),
        **({"node": node} if node else {}),
        **({"data": data} if data else {}),
    }

    # 收集所有需要发送的连接
    all_connections: list[WebSocket] = []
    all_connections.extend(_global_ws_connections)
    per_ticket = _ws_connections.get(ticket_id, [])
    for ws in per_ticket:
        if ws not in all_connections:
            all_connections.append(ws)

    # 向所有连接发送，清理断开的连接
    disconnected_global: list[WebSocket] = []
    disconnected_ticket: list[WebSocket] = []

    for ws in all_connections:
        try:
            await ws.send_json(payload)
        except Exception:
            if ws in _global_ws_connections:
                disconnected_global.append(ws)
            else:
                disconnected_ticket.append(ws)

    for ws in disconnected_global:
        _global_ws_connections.remove(ws)

    for ws in disconnected_ticket:
        per_ticket.remove(ws)
    if not per_ticket and ticket_id in _ws_connections:
        _ws_connections.pop(ticket_id, None)


async def _broadcast_review_event(
    event_type: str,
    ticket_id: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """向所有 /ws/monitor 连接广播人工审核事件。

    event_type:
        - "review_requested": 工单刚转入人工审核
        - "review_decided": 审核员已提交决策
    """
    now = datetime.now()
    message: dict[str, Any] = {
        "type": event_type,
        "ticket_id": ticket_id,
        "timestamp": now.isoformat(),
    }
    if payload:
        message.update(payload)

    disconnected: list[WebSocket] = []
    for ws in _global_ws_connections:
        try:
            await ws.send_json(message)
        except Exception:  # noqa: BLE001
            disconnected.append(ws)
    for ws in disconnected:
        if ws in _global_ws_connections:
            _global_ws_connections.remove(ws)
