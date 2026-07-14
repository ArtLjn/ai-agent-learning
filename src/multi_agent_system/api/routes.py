"""API 路由模块，提供工单 CRUD、知识库上传、统计查询和 WebSocket 实时推送。"""

import asyncio
import json
import random
import re
from datetime import datetime
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from loguru import logger

from src.multi_agent_system.core import CachedLLMClient
from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.logging import generate_trace_id
from src.multi_agent_system.core.permissions import require_role
from src.multi_agent_system.tools.rag_client import RagServiceUnavailable
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

_COMPANY_NAME = "云舟科技有限公司"
_COMPANY_NAME_QUERY_RE = re.compile(
    r"(公司\s*(?:叫啥|叫什么|叫什[么麼]|名称|名字|名)|你们公司|贵司)"
)
_INTERNAL_REFERENCE_SENTENCE_RE = re.compile(
    r"[^。；\n]*(?:Trace|RAG|Prompt|Token|系统运维管理端|工单提交人默认为|服务台负责人工审核兜底)[^。；\n]*[。；]?"
)
_REFERENCE_MAINTENANCE_TERMS = (
    "员工说法",
    "推荐关键词",
    "云舟科技员工服务总览",
)

# WebSocket 连接管理：ticket_id -> 订阅该工单的 WebSocket 列表
_ws_connections: dict[str, list[WebSocket]] = {}

# 全局 WebSocket 连接：接收所有工单状态更新
_global_ws_connections: list[WebSocket] = []

_MOCK_CATEGORY_GUIDANCE = {
    TicketCategory.INQUIRY.value: {
        "label": "制度流程咨询",
        "instruction": "生成制度流程咨询问题：员工主要询问公司制度、入口、审批材料、办理路径或操作方法。必须围绕云舟科技内部员工服务场景，不要生成系统运维、代码定位、RSS/OOM、退款订阅或外部客户问题。",
    },
    TicketCategory.TECHNICAL.value: {
        "label": "IT 与办公支持",
        "instruction": "生成 IT 与办公支持问题：员工遇到 CloudID、YunVPN、邮箱、电脑、打印机、会议室设备、网络或办公软件问题。不要生成后端代码、RSS 内存、Kubernetes、Redis、API 网关等运维研发问题。",
    },
    TicketCategory.BILLING.value: {
        "label": "费用薪酬报销",
        "instruction": "生成费用薪酬报销问题：员工围绕报销、差旅、餐补、工资单、社保公积金、发票、付款节点或 FinFlow/PeopleHub 状态求助。不要生成退款、订阅、套餐、外部付费产品问题。",
    },
    TicketCategory.COMPLAINT.value: {
        "label": "风险投诉升级",
        "instruction": "生成风险投诉升级问题：员工对服务台处理、行政服务、设备支持、安全合规、离职权限或数据风险表达不满或要求升级。不要生成外部客户投诉、全站故障或赔偿问题。",
    },
}

_MOCK_RAG_QUERY_BY_CATEGORY = {
    TicketCategory.INQUIRY.value: "云舟科技 员工 制度 入口 审批 材料 办理 小舟助手 PeopleHub BuyDesk",
    TicketCategory.TECHNICAL.value: "云舟科技 CloudID YunVPN 邮箱 电脑 打印机 会议室 网络 办公设备",
    TicketCategory.BILLING.value: "云舟科技 报销 差旅 餐补 工资单 社保 公积金 FinFlow PeopleHub",
    TicketCategory.COMPLAINT.value: "云舟科技 安全合规 DLP 设备丢失 离职权限 服务台 升级 投诉",
}

_EMPLOYEE_MOCK_KEYWORDS = (
    "云舟",
    "员工",
    "小舟助手",
    "PeopleHub",
    "FinFlow",
    "BuyDesk",
    "AssetOne",
    "CloudID",
    "YunVPN",
    "LearnHub",
    "服务台",
    "餐补",
    "考勤",
    "报销",
    "工位",
    "门禁",
    "访客",
    "薪酬",
    "社保",
    "公积金",
    "合同",
    "用印",
    "培训",
    "绩效",
    "OKR",
)

_MOCK_FORBIDDEN_PATTERN = re.compile(
    r"RSS|OOM|内存泄漏|内存上涨|Kubernetes|Redis|API\s*网关|数据库连接池|"
    r"消息队列|CI/CD|Pod|CrashLoopBackOff|退款|订阅|套餐|外部客户|客户投诉|"
    r"阿里云|OSS|商品|全站故障",
    re.IGNORECASE,
)


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


def _strip_reference_metadata(text: str) -> str:
    """移除用户不可见的检索元信息，保留可读的知识内容。"""
    text = re.sub(r"检索到以下知识片段[:：]?", "", text)
    text = re.sub(
        r"\b\d+\.\s*标题:\s*[^；。]+；\s*分类:\s*[^；。]+；\s*相似度:\s*\d+(?:\.\d+)?\s*内容:\s*",
        "\n",
        text,
    )
    text = re.sub(
        r"标题:\s*[^；。]+；\s*分类:\s*[^；。]+；\s*相似度:\s*\d+(?:\.\d+)?\s*内容:\s*",
        "",
        text,
    )
    text = re.sub(r"相似度:\s*\d+(?:\.\d+)?", "", text)
    text = _INTERNAL_REFERENCE_SENTENCE_RE.sub("", text)
    text = _strip_employee_reference_noise(text)
    return re.sub(r"\s+", " ", text).strip(" ；。")


def _strip_employee_reference_noise(text: str) -> str:
    """去掉员工侧不应看到的文档来源、分类和知识库维护字段。"""
    for term in _REFERENCE_MAINTENANCE_TERMS:
        text = text.replace(term, " ")
    text = re.sub(r"\b[\w.-]+\.md\b", " ", text)
    text = re.sub(r"\b(?:inquiry|technical|billing|complaint)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bP[1-4]\s*[:：][^。；\n]*", " ", text)
    text = re.sub(r"\|\s*-{2,}\s*(?:\|\s*-{2,}\s*)+\|?", " ", text)
    text = re.sub(r"(?:^|\s)-{2,}(?:\s+-{2,})+(?=\s|$)", " ", text)
    text = text.replace("|", " ")
    text = re.sub(r"(?:^|\s)-\s*", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_company_name_query(content: str) -> bool:
    """识别员工询问公司名称的简单事实问题。"""
    return bool(_COMPANY_NAME_QUERY_RE.search(content or ""))


def _sanitize_processing_result_for_user(
    result: str | None,
    content: str = "",
) -> str | None:
    """用户视角清洗历史处理结果，避免暴露知识库命中、相似度等内部字段。"""
    if not result:
        return result

    if _is_company_name_query(content) and _COMPANY_NAME in result:
        return f"您好，公司名称是{_COMPANY_NAME}。"

    internal_markers = (
        "知识库命中",
        "知识库参考",
        "检索到以下知识片段",
        "相似度",
        "置信度",
        "需要人工确认",
        "Trace",
        "RAG",
        "Prompt",
        "Token",
        "Key/Secret",
        "接口返回码",
    )
    if not any(marker in result for marker in internal_markers):
        return result

    body = result
    if "知识库参考" in body:
        body = body.split("知识库参考", 1)[1]
    if "建议先核对" in body:
        body = body.split("建议先核对", 1)[0]
    if "需要人工确认" in body:
        body = body.split("需要人工确认", 1)[0]

    summary = _strip_reference_metadata(body)
    if len(summary) > 520:
        summary = f"{summary[:520].rstrip()}..."
    if not summary:
        summary = "可以先核对员工服务制度、内部平台入口和归口部门说明。"

    return (
        "您好，可以先按以下信息处理：\n\n"
        f"{summary}\n\n"
        "如果还需要继续处理，建议补充员工号、涉及系统或平台、发生时间、截图或审批单号，"
        "云舟服务台会按归口部门继续处理。"
    )


def _extract_user_ticket_content(content: str) -> str:
    """user 视角只返回原始提交内容，不暴露意图识别后的结构化标签。"""
    marker = "【原始描述】"
    if marker not in content:
        return content.strip()
    return content.rsplit(marker, 1)[-1].strip()


def _build_ticket_response(ticket: dict[str, Any], *, role: str) -> TicketResponse:
    """按角色构造工单响应；user 视角过滤内部处理细节。"""
    processing_result = ticket.get("processing_result")
    content = ticket.get("content", "")
    references = _parse_references(ticket)
    review_score = ticket.get("review_score")
    retry_count = ticket.get("retry_count", 0)
    if role == "user":
        content = _extract_user_ticket_content(content)
        processing_result = _sanitize_processing_result_for_user(processing_result, content)
        references = []
        review_score = None
        retry_count = 0

    return TicketResponse(
        ticket_id=ticket.get("ticket_id", ""),
        content=content,
        service_type=ticket.get("service_type"),
        key_materials=ticket.get("key_materials") or {},
        category=ticket.get("category"),
        priority=ticket.get("priority"),
        processing_result=processing_result,
        references=references,
        review_score=review_score,
        retry_count=retry_count,
        status=ticket.get("status", "received"),
        error=ticket.get("error"),
        satisfied=ticket.get("satisfied"),
        created_at=ticket.get("created_at", datetime.now()),
    )


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
        if isinstance(doc, dict) and _is_employee_mock_document(doc)
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


async def _load_mock_question_documents(
    request: Request,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """优先从 rag-service ticket_knowledge 检索员工知识库片段。"""
    rag_client = getattr(request.app.state, "rag_client", None)
    settings = getattr(request.app.state, "settings", Settings())
    collection = getattr(settings, "rag_service_collection", "ticket_knowledge")
    query = _MOCK_RAG_QUERY_BY_CATEGORY.get(
        category or "",
        "云舟科技 员工服务台 制度 办公 报销 权限 行政 IT 安全",
    )

    if rag_client is not None:
        try:
            chunks, _debug = await rag_client.retrieve(
                query=query,
                collection=collection,
                mode="hybrid",
                top_k=20,
                use_hyde=False,
            )
            docs = [_mock_doc_from_rag_chunk(chunk) for chunk in chunks]
            docs = [doc for doc in docs if _is_employee_mock_document(doc)]
            if docs:
                return docs
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"mock 工单读取 rag-service 失败，尝试本地知识库: {exc}")

    knowledge_tool = getattr(request.app.state, "knowledge_tool", None)
    try:
        docs_resp = knowledge_tool.list_documents(limit=100) if knowledge_tool else {}
        docs = docs_resp.get("documents", []) if isinstance(docs_resp, dict) else []
        return [doc for doc in docs if isinstance(doc, dict)]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"mock 工单读取本地知识库失败，使用兜底问题: {exc}")
        return []


def _mock_doc_from_rag_chunk(chunk: Any) -> dict[str, Any]:
    """把 rag-service 检索片段转成 mock 问题生成所需文档结构。"""
    metadata = getattr(chunk, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    content = str(getattr(chunk, "content", "") or "").strip()
    source = str(metadata.get("source") or metadata.get("file_name") or "").strip()
    title = str(metadata.get("title") or "").strip()
    if not title and source:
        title = source.rsplit("/", 1)[-1].replace(".md", "")
    return {
        "id": getattr(chunk, "id", None),
        "title": title or "云舟科技员工服务知识",
        "category": metadata.get("category") or source or "ticket_knowledge",
        "source": source,
        "content": content,
        "preview": content,
        "chunks": [{"content": content, "metadata": metadata}],
    }


def _is_employee_mock_document(doc: dict[str, Any]) -> bool:
    """过滤旧技术运维或外部客服知识，避免生成不符合当前主题的问题。"""
    text = " ".join(
        str(value)
        for value in (
            doc.get("title"),
            doc.get("category"),
            doc.get("source"),
            doc.get("preview"),
            doc.get("content"),
            _mock_doc_text(doc),
        )
        if value
    )
    if not text.strip() or _MOCK_FORBIDDEN_PATTERN.search(text):
        return False
    return any(keyword in text for keyword in _EMPLOYEE_MOCK_KEYWORDS)


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
                    "你是云舟科技员工服务台的演示数据生成 Agent。请根据给定知识库片段，"
                    "生成一条企业内部员工会提交的中文服务请求。要求："
                    "1. 不要复述知识库答案；2. 像用户遇到问题在求助；"
                    "3. 30 到 80 字；4. 不要编号，不要解释，只输出问题本身；"
                    "5. 可以自然包含员工号、日期、平台名、审批单号或影响范围；"
                    "6. 必须围绕云舟科技内部员工服务，不要写代码定位、RSS/OOM、"
                    "Kubernetes、Redis、API 网关、退款订阅、外部客户或商品售后；"
                    f"7. 本次必须生成{category_label}工单。{category_instruction}"
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
    question = _clean_mock_question(str(raw))
    return question if _is_valid_employee_mock_question(question) else ""


def _clean_mock_question(value: str) -> str:
    """清理模型输出，避免带引号、列表符号或解释性前缀。"""
    text = value.strip().strip('"').strip("'").strip()
    text = text.removeprefix("问题：").removeprefix("工单问题：").strip()
    for prefix in ("-", "1.", "1、"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text[:120]


def _is_valid_employee_mock_question(question: str) -> bool:
    """校验 LLM 输出仍符合员工服务台主题。"""
    if not question or _MOCK_FORBIDDEN_PATTERN.search(question):
        return False
    return any(keyword in question for keyword in _EMPLOYEE_MOCK_KEYWORDS)


def _fallback_mock_question(
    doc: dict[str, Any] | None = None,
    category: str | None = None,
) -> str:
    """LLM 不可用时的随机兜底问题。"""
    if category == TicketCategory.INQUIRY.value:
        variants = [
            "我想咨询云舟科技加班餐补在哪里查看，需要先提交钉钉加班审批吗？",
            "新员工入职第一天找不到制度入口，小舟助手和云舟员工门户应该用哪个？",
            "办公用品和软件许可证分别在哪个平台申请，需要准备哪些审批材料？",
        ]
        return random.SystemRandom().choice(variants)
    if category == TicketCategory.TECHNICAL.value:
        variants = [
            "我今天登录 CloudID 后提示 MFA 验证失败，换了手机后进不了 PeopleHub，请帮忙处理。",
            "YunVPN 显示已连接，但打不开云舟员工门户和 FinFlow，麻烦帮我排查。",
            "上海 A 座 4F 打印机一直显示离线，影响我打印合同材料，请帮忙看看。",
        ]
        return random.SystemRandom().choice(variants)
    if category == TicketCategory.BILLING.value:
        variants = [
            "我昨晚加班审批已通过，但 PeopleHub 里没有餐补记录，请帮我核查。",
            "FinFlow 报销单被退回，提示发票抬头不一致，我需要补哪些材料？",
            "本月工资单里社保公积金城市显示不对，想请 HR 薪酬福利组确认。",
        ]
        return random.SystemRandom().choice(variants)
    if category == TicketCategory.COMPLAINT.value:
        variants = [
            "离职同事还能访问 CloudID 和财务系统，这个风险比较高，请尽快升级处理。",
            "我反馈会议室设备故障两次都没人处理，今天客户会议又受影响，请升级跟进。",
            "DLP 拦截后我不确定是否误报，但服务台回复不清楚，希望安全团队确认。",
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
async def create_ticket(
    body: TicketCreate,
    request: Request,
    _role_check: dict = Depends(require_role("user")),
) -> dict:
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

    # user_id 优先取登录 session（防伪造），演示模式或前端显式传入时才用 body.user_id
    from src.multi_agent_system.core.auth import get_current_user
    session_user = get_current_user(request)
    effective_user_id = (
        session_user.get("user_id")
        if session_user and session_user.get("user_id")
        else (body.user_id or "anonymous")
    )

    ticket_data = {
        "ticket_id": ticket_id,
        "content": intent["content"],
        "user_id": effective_user_id,
        "service_type": body.service_type,
        "key_materials": body.key_materials,
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
async def create_batch_tickets(
    body: BatchTicketCreate,
    request: Request,
    _role_check: dict = Depends(require_role("user")),
) -> dict:
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
        # user_id 注入策略同单条 create_ticket：session 优先，body 兜底，再 fallback 'anonymous'
        from src.multi_agent_system.core.auth import get_current_user
        session_user = get_current_user(request)
        effective_user_id = (
            session_user.get("user_id")
            if session_user and session_user.get("user_id")
            else (ticket.user_id or "anonymous")
        )

        db_tool = request.app.state.db_tool
        ticket_data = {
            "ticket_id": ticket_id,
            "content": ticket.content,
            "user_id": effective_user_id,
            "service_type": ticket.service_type,
            "key_materials": ticket.key_materials,
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
    _role_check: dict = Depends(require_role("user")),
) -> dict:
    """基于知识库随机片段生成一条自然用户口吻的 mock 工单问题。"""
    requested_category = category.value if category else None
    docs = await _load_mock_question_documents(request, requested_category)

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
async def get_ticket(
    ticket_id: str,
    request: Request,
    _role_check: dict = Depends(require_role("user", "admin")),
) -> TicketResponse:
    """根据 ticket_id 查询工单详情。"""
    db_tool = request.app.state.db_tool
    ticket = await db_tool.get_ticket(ticket_id)

    if ticket is None:
        raise HTTPException(status_code=404, detail=f"工单 {ticket_id} 不存在")

    # 用户隔离：user 角色只能看自己的工单
    from src.multi_agent_system.core.auth import assert_ticket_access, get_session_role
    assert_ticket_access(ticket, request)

    return _build_ticket_response(
        {**ticket, "ticket_id": ticket.get("ticket_id", ticket_id)},
        role=get_session_role(request),
    )


@router.get("/tickets/{ticket_id}/messages", response_model=list[dict])
async def list_ticket_messages(
    ticket_id: str,
    request: Request,
    _role_check: dict = Depends(require_role("user", "admin")),
) -> list[dict]:
    """获取工单沟通记录。"""
    ticket = await request.app.state.db_manager.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"工单 {ticket_id} 不存在")
    # 用户隔离校验
    from src.multi_agent_system.core.auth import assert_ticket_access
    assert_ticket_access(ticket, request)
    return await request.app.state.db_manager.list_ticket_messages(ticket_id)


@router.post("/tickets/{ticket_id}/messages", response_model=dict)
async def create_user_ticket_message(
    ticket_id: str,
    body: TicketMessageCreate,
    request: Request,
    _role_check: dict = Depends(require_role("user")),
) -> dict:
    """用户补充工单信息，并恢复后续处理。"""
    ticket = await request.app.state.db_manager.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"工单 {ticket_id} 不存在")
    # 用户隔离校验
    from src.multi_agent_system.core.auth import assert_ticket_access
    assert_ticket_access(ticket, request)
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
    _role_check: dict = Depends(require_role("user", "admin")),
) -> list[TicketResponse]:
    """工单列表查询，支持状态和分类过滤以及分页。

    用户隔离：user 角色只看自己的工单；admin/developer 看全部。
    """
    db_tool = request.app.state.db_tool
    # user 角色按 session.user_id 过滤；admin/developer 不过滤
    from src.multi_agent_system.core.auth import get_session_role, get_session_user_id
    role = get_session_role(request)
    filter_user_id = (
        get_session_user_id(request) if role == "user" else None
    )
    tickets: list[dict[str, Any]] = await db_tool.list_tickets(
        status=status,
        category=category,
        user_id=filter_user_id,
        limit=limit,
        offset=offset,
    )

    return [_build_ticket_response(t, role=role) for t in tickets]


# ============================================================
# 知识库接口
# ============================================================


@router.get("/knowledge", response_model=dict)
async def list_knowledge(
    request: Request,
    page: int = Query(default=1, ge=1, description="页码（1-based）"),
    page_size: int = Query(default=50, ge=1, le=100, description="每页文档数"),
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """列出服务台知识维护文档，合并本地发布状态与 rag-service 元数据。

    响应字段：{total, page, page_size, documents[]}。
    documents 各项含 doc_id/source/category/chunk_count/status/version/ingested_at 等。
    """
    rag_client = request.app.state.rag_client
    settings = request.app.state.settings
    db_manager = request.app.state.db_manager
    collection = settings.rag_service_collection

    local = await db_manager.list_knowledge_documents(
        collection=collection,
        page=page,
        page_size=page_size,
    )

    if rag_client is None:
        if local["total"] > 0:
            return local
        raise HTTPException(status_code=503, detail="rag-service 客户端未初始化")

    try:
        remote = await rag_client.list_documents(
            collection=collection,
            page=page,
            page_size=page_size,
        )
    except RagServiceUnavailable as e:
        logger.warning(f"rag-service list_documents 失败: {e}")
        if local["total"] > 0:
            return local
        raise HTTPException(status_code=503, detail=str(e)) from e

    local_docs = local.get("documents") or []
    local_ids = {doc.get("doc_id") for doc in local_docs}
    merged = list(local_docs)
    for doc in remote.get("documents") or []:
        if doc.get("doc_id") in local_ids:
            continue
        extra = dict(doc.get("extra") or {})
        extra.setdefault("status", "published")
        extra.setdefault("version", 1)
        merged.append({
            **doc,
            "title": doc.get("title") or doc.get("source") or doc.get("doc_id"),
            "status": "published",
            "version": 1,
            "extra": extra,
        })
    return {
        "total": max(int(remote.get("total") or 0), len(merged), int(local.get("total") or 0)),
        "page": page,
        "page_size": page_size,
        "documents": merged[:page_size],
    }


@router.post("/knowledge", response_model=dict)
async def upload_knowledge(
    request: Request,
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """上传知识文档并记录服务台发布状态，支持 text 与 file 两种模式。

    按 content-type 分发：
    - application/json → text 模式：body 含 title/content/category，调 ingest_text
    - multipart/form-data → file 模式：含 file 字段（PDF/MD/TXT），调 ingest_file

    响应字段：{status, doc_id, chunk_count, collection, action}。
    """
    rag_client = request.app.state.rag_client
    settings = request.app.state.settings
    db_manager = request.app.state.db_manager
    collection = settings.rag_service_collection

    if rag_client is None:
        raise HTTPException(status_code=503, detail="rag-service 客户端未初始化")

    content_type = request.headers.get("content-type", "")

    if content_type.startswith("application/json"):
        try:
            body = await request.json()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"JSON 解析失败: {e}") from e

        text = body.get("content") or ""
        title = body.get("title") or "(无标题)"
        category = body.get("category") or "default"

        if not text.strip():
            raise HTTPException(status_code=400, detail="content 为必填字段")

        try:
            result = await rag_client.ingest_text(
                text=text,
                collection=collection,
                source=f"admin-upload:{title}",
                category=category,
            )
        except RagServiceUnavailable as e:
            failed_doc_id = f"KD-{generate_trace_id()}"
            await db_manager.upsert_knowledge_document(
                doc_id=failed_doc_id,
                title=title,
                category=category,
                source=f"admin-upload:{title}",
                collection=collection,
                status="failed",
                content=text,
                chunk_count=0,
                version=1,
                metadata={"error": str(e), "status": "failed"},
            )
            await db_manager.create_knowledge_version(
                doc_id=failed_doc_id,
                version=1,
                title=title,
                category=category,
                content=text,
                collection=collection,
                status="failed",
                chunk_count=0,
                metadata={"error": str(e)},
            )
            logger.warning(f"rag-service ingest_text 失败: {e}")
            raise HTTPException(status_code=503, detail=str(e)) from e

        doc_id = str(result.get("doc_id") or f"KD-{generate_trace_id()}")
        metadata = {"rag_result": result, "status": "published", "version": 1}
        record = await db_manager.upsert_knowledge_document(
            doc_id=doc_id,
            title=title,
            category=category,
            source=result.get("source") or f"admin-upload:{title}",
            collection=result.get("collection") or collection,
            status="published",
            content=text,
            chunk_count=int(result.get("chunk_count") or 0),
            version=1,
            metadata=metadata,
        )
        await db_manager.create_knowledge_version(
            doc_id=doc_id,
            version=1,
            title=title,
            category=category,
            content=text,
            collection=result.get("collection") or collection,
            status="published",
            chunk_count=int(result.get("chunk_count") or 0),
            rag_doc_id=doc_id,
            metadata=metadata,
        )
        return {"status": "published", "action": result.get("action"), **record}

    if content_type.startswith("multipart/form-data"):
        try:
            form = await request.form()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"form 解析失败: {e}") from e

        upload_file = form.get("file")
        title = form.get("title") or "(无标题)"
        category = form.get("category") or "default"

        if upload_file is None:
            raise HTTPException(status_code=400, detail="file 字段为必填")

        file_bytes = await upload_file.read()
        filename = upload_file.filename or "upload"

        try:
            result = await rag_client.ingest_file(
                file_bytes=file_bytes,
                filename=filename,
                collection=collection,
                source=title,
                category=category,
            )
        except RagServiceUnavailable as e:
            failed_doc_id = f"KD-{generate_trace_id()}"
            await db_manager.upsert_knowledge_document(
                doc_id=failed_doc_id,
                title=str(title),
                category=str(category),
                source=str(title or filename),
                collection=collection,
                status="failed",
                content=None,
                chunk_count=0,
                version=1,
                metadata={"error": str(e), "filename": filename, "status": "failed"},
            )
            await db_manager.create_knowledge_version(
                doc_id=failed_doc_id,
                version=1,
                title=str(title),
                category=str(category),
                content=None,
                collection=collection,
                status="failed",
                metadata={"error": str(e), "filename": filename},
            )
            logger.warning(f"rag-service ingest_file 失败: {e}")
            raise HTTPException(status_code=503, detail=str(e)) from e

        doc_id = str(result.get("doc_id") or f"KD-{generate_trace_id()}")
        metadata = {"rag_result": result, "filename": filename, "status": "published", "version": 1}
        record = await db_manager.upsert_knowledge_document(
            doc_id=doc_id,
            title=str(title),
            category=str(category),
            source=result.get("source") or str(title or filename),
            collection=result.get("collection") or collection,
            status="published",
            content=None,
            chunk_count=int(result.get("chunk_count") or 0),
            version=1,
            metadata=metadata,
        )
        await db_manager.create_knowledge_version(
            doc_id=doc_id,
            version=1,
            title=str(title),
            category=str(category),
            content=None,
            collection=result.get("collection") or collection,
            status="published",
            chunk_count=int(result.get("chunk_count") or 0),
            rag_doc_id=doc_id,
            metadata=metadata,
        )
        return {"status": "published", "action": result.get("action"), **record}

    raise HTTPException(
        status_code=400,
        detail=f"不支持的 content-type: {content_type}（仅 application/json 或 multipart/form-data）",
    )


@router.patch("/knowledge/{doc_id}", response_model=dict)
async def update_knowledge(
    doc_id: str,
    body: dict[str, Any],
    request: Request,
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """更新知识内容并创建新的发布版本。"""
    rag_client = request.app.state.rag_client
    settings = request.app.state.settings
    db_manager = request.app.state.db_manager
    collection = settings.rag_service_collection

    if rag_client is None:
        raise HTTPException(status_code=503, detail="rag-service 客户端未初始化")

    existing = await db_manager.get_knowledge_document(doc_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"知识文档 {doc_id} 不存在")

    content = str(body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content 为必填字段")
    title = str(body.get("title") or existing.get("title") or existing.get("source") or doc_id)
    category = str(body.get("category") or existing.get("category") or "default")
    version = await db_manager.next_knowledge_version(doc_id)

    try:
        result = await rag_client.ingest_text(
            text=content,
            collection=collection,
            source=f"admin-update:{title}",
            category=category,
        )
    except RagServiceUnavailable as e:
        logger.warning(f"rag-service update ingest_text 失败: {e}")
        raise HTTPException(status_code=503, detail=str(e)) from e

    metadata = {
        "rag_result": result,
        "rag_doc_id": result.get("doc_id"),
        "status": "published",
        "version": version,
    }
    record = await db_manager.upsert_knowledge_document(
        doc_id=doc_id,
        title=title,
        category=category,
        source=existing.get("source") or f"admin-upload:{title}",
        collection=result.get("collection") or collection,
        status="published",
        content=content,
        chunk_count=int(result.get("chunk_count") or 0),
        version=version,
        metadata=metadata,
    )
    await db_manager.create_knowledge_version(
        doc_id=doc_id,
        version=version,
        title=title,
        category=category,
        content=content,
        collection=result.get("collection") or collection,
        status="published",
        chunk_count=int(result.get("chunk_count") or 0),
        rag_doc_id=result.get("doc_id"),
        metadata=metadata,
    )
    return {"status": "published", "action": result.get("action"), **record}


@router.get("/knowledge/{doc_id}/versions", response_model=dict)
async def list_knowledge_versions(
    doc_id: str,
    request: Request,
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """查看知识文档版本历史。"""
    versions = await request.app.state.db_manager.list_knowledge_versions(doc_id)
    if not versions:
        raise HTTPException(status_code=404, detail=f"知识文档 {doc_id} 不存在")
    return {"doc_id": doc_id, "versions": versions}


@router.post("/knowledge/{doc_id}/rollback", response_model=dict)
async def rollback_knowledge(
    doc_id: str,
    body: dict[str, Any],
    request: Request,
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """回滚到指定历史版本，并创建新的 active 版本。"""
    rag_client = request.app.state.rag_client
    settings = request.app.state.settings
    db_manager = request.app.state.db_manager
    collection = settings.rag_service_collection

    if rag_client is None:
        raise HTTPException(status_code=503, detail="rag-service 客户端未初始化")
    target_version = int(body.get("version") or 0)
    target = await db_manager.get_knowledge_version(doc_id, target_version)
    if target is None:
        raise HTTPException(status_code=404, detail=f"知识版本 {target_version} 不存在")
    content = str(target.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=409, detail="目标版本没有可回滚的文本内容")

    new_version = await db_manager.next_knowledge_version(doc_id)
    title = str(target.get("title") or doc_id)
    category = target.get("category")
    try:
        result = await rag_client.ingest_text(
            text=content,
            collection=collection,
            source=f"rollback:{title}:v{target_version}",
            category=category,
        )
    except RagServiceUnavailable as e:
        logger.warning(f"rag-service rollback ingest_text 失败: {e}")
        raise HTTPException(status_code=503, detail=str(e)) from e

    metadata = {
        "rag_result": result,
        "rag_doc_id": result.get("doc_id"),
        "rolled_back_from_version": target_version,
        "status": "published",
        "version": new_version,
    }
    record = await db_manager.upsert_knowledge_document(
        doc_id=doc_id,
        title=title,
        category=category,
        source=f"rollback:{title}:v{target_version}",
        collection=result.get("collection") or collection,
        status="published",
        content=content,
        chunk_count=int(result.get("chunk_count") or 0),
        version=new_version,
        metadata=metadata,
    )
    await db_manager.create_knowledge_version(
        doc_id=doc_id,
        version=new_version,
        title=title,
        category=category,
        content=content,
        collection=result.get("collection") or collection,
        status="published",
        chunk_count=int(result.get("chunk_count") or 0),
        rag_doc_id=result.get("doc_id"),
        metadata=metadata,
    )
    return {
        "status": "published",
        "rolled_back_from_version": target_version,
        "action": result.get("action"),
        **record,
    }


@router.delete("/knowledge/{doc_id}", response_model=dict)
async def delete_knowledge(
    doc_id: str,
    request: Request,
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """删除 rag-service 中指定 doc_id 的文档（Qdrant points + SQLite metadata）。"""
    rag_client = request.app.state.rag_client
    settings = request.app.state.settings

    if rag_client is None:
        raise HTTPException(status_code=503, detail="rag-service 客户端未初始化")

    try:
        result = await rag_client.delete_document(
            collection=settings.rag_service_collection,
            doc_id=doc_id,
        )
    except RagServiceUnavailable as e:
        logger.warning(f"rag-service delete_document 失败: {e}")
        raise HTTPException(status_code=503, detail=str(e)) from e

    return {"status": "ok", **result}


@router.get("/knowledge/evaluation", response_model=dict)
async def get_knowledge_evaluation(
    request: Request,
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """获取 rag-service 最新 RAG 召回评测报告。"""
    rag_client = request.app.state.rag_client

    if rag_client is None:
        raise HTTPException(status_code=503, detail="rag-service 客户端未初始化")

    try:
        return await rag_client.latest_evaluation()
    except RagServiceUnavailable as e:
        logger.warning(f"rag-service latest_evaluation 失败: {e}")
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/knowledge/evaluation/run", response_model=dict)
async def run_knowledge_evaluation(
    request: Request,
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """触发 rag-service 运行 RAG 召回评测。"""
    rag_client = request.app.state.rag_client

    if rag_client is None:
        raise HTTPException(status_code=503, detail="rag-service 客户端未初始化")

    try:
        body = await request.json()
    except ValueError:
        body = {}

    try:
        return await rag_client.run_evaluation(
            mode=body.get("mode") or "hybrid",
            top_k=int(body.get("top_k") or 10),
            k_values=body.get("k_values") or [1, 3, 5, 10],
        )
    except RagServiceUnavailable as e:
        logger.warning(f"rag-service run_evaluation 失败: {e}")
        raise HTTPException(status_code=503, detail=str(e)) from e


# ============================================================
# 统计接口
# ============================================================


@router.get("/settings", response_model=dict)
async def get_system_settings(
    request: Request,
    _role_check: dict = Depends(require_role("developer")),
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
    _role_check: dict = Depends(require_role("user")),
) -> dict:
    """提交用户对工单处理结果的满意度反馈。

    satisfied=false 且工单已 completed 时，自动创建 user_request 类型 pending
    审核单，将工单转回人工审核队列。
    """
    from datetime import datetime

    from src.multi_agent_system.core.evaluation import EvaluationCollector
    from src.multi_agent_system.core.logging import generate_trace_id

    satisfied = body.get("satisfied", False)
    reason = str(body.get("reason") or "").strip()
    app = request.app
    db_manager = app.state.db_manager
    db_tool = app.state.db_tool

    # 用户隔离校验
    ticket = await db_tool.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"工单 {ticket_id} 不存在")
    from src.multi_agent_system.core.auth import assert_ticket_access, get_session_role
    assert_ticket_access(ticket, request)
    if Settings().auth_enabled and get_session_role(request) != "user":
        raise HTTPException(status_code=403, detail="仅工单所属用户可提交满意度反馈")
    if ticket.get("satisfied") is not None:
        raise HTTPException(status_code=409, detail="该工单已提交过最终反馈")
    if ticket.get("status") != "completed":
        raise HTTPException(status_code=409, detail="仅已完成工单可提交最终反馈")
    if not satisfied and not reason:
        raise HTTPException(status_code=422, detail="不满意反馈必须填写申诉原因")

    collector = EvaluationCollector(db_manager=db_manager)

    try:
        await collector.record_user_feedback(ticket_id, satisfied)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # 不满意 + 工单已完成 -> 自动转人工审核
    if not satisfied:
        ticket = await db_manager.get_ticket(ticket_id) or ticket
        if ticket and ticket.get("status") == "completed":
            coordinator = getattr(app.state, "coordinator", None)
            ai_suggestion = None
            if coordinator is not None:
                try:
                    ai_suggestion = await coordinator.suggest_decision(
                        ticket_id,
                        "user_request",
                        reason,
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
                "trigger_reason": reason,
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
    status: str | None = Query(default=None),
    trigger_type: str | None = Query(default=None),
    category: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """查询服务台待办队列，按最新待办更新时间倒序返回。"""
    db_manager = request.app.state.db_manager
    rows = await db_manager.list_pending_reviews_with_tickets(
        status=status,
        trigger_type=trigger_type,
        category=category,
        priority=priority,
        limit=limit,
        offset=offset,
    )
    total = await db_manager.count_pending_reviews(
        status=status,
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
            "status": row.get("ticket_status"),
            "ai_suggestion": _parse_ai_suggestion(row.get("ai_suggestion")),
            "waiting_seconds": waiting_seconds,
            "created_at": created_at_str,
            "_updated_ts": created_at.timestamp(),
        })

    # 兜底排序（DB 已排过，二次保险）：最新待办优先，时间相同再看优先级。
    queue.sort(
        key=lambda r: (
            -(r.get("_updated_ts") or 0),
            _PRIORITY_ORDER.get(r.get("priority") or "", 9),
        )
    )
    for item in queue:
        item.pop("_updated_ts", None)

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
    messages = await db_manager.list_ticket_messages(ticket_id)
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
        "messages": messages,
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
async def get_analytics(
    request: Request,
    _role_check: dict = Depends(require_role("admin")),
) -> dict:
    """获取统计面板数据：分类分布 + 优先级分布 + 处理统计 + 评估指标。"""
    from src.multi_agent_system.core.evaluation import EvaluationCollector

    db_manager = request.app.state.db_manager
    analytics_tool = request.app.state.analytics_tool
    collector = EvaluationCollector(db_manager=db_manager)
    category_distribution = (
        await analytics_tool.get_category_distribution()
        if hasattr(analytics_tool, "get_category_distribution")
        and asyncio.iscoroutinefunction(analytics_tool.get_category_distribution)
        else await db_manager.get_category_distribution()
    )
    priority_distribution = (
        await analytics_tool.get_priority_distribution()
        if hasattr(analytics_tool, "get_priority_distribution")
        and asyncio.iscoroutinefunction(analytics_tool.get_priority_distribution)
        else await db_manager.get_priority_distribution()
    )
    resolution_stats = (
        await analytics_tool.get_resolution_stats()
        if hasattr(analytics_tool, "get_resolution_stats")
        and asyncio.iscoroutinefunction(analytics_tool.get_resolution_stats)
        else await db_manager.get_resolution_stats()
    )
    daily_stats = (
        await analytics_tool.get_daily_stats()
        if hasattr(analytics_tool, "get_daily_stats")
        and asyncio.iscoroutinefunction(analytics_tool.get_daily_stats)
        else []
    )
    review_quality = await db_manager.get_review_workbench_stats()
    feedback = await db_manager.get_feedback_summary()

    return {
        "category_distribution": category_distribution,
        "priority_distribution": priority_distribution,
        "resolution_stats": resolution_stats,
        "daily_stats": daily_stats,
        "efficiency": await collector.get_efficiency_stats(),
        "evaluation": await collector.get_evaluation_summary(),
        "service_desk": {
            "status_summary": await db_manager.get_ticket_status_summary(),
            "review_quality": review_quality,
            "feedback_summary": {
                "satisfied": feedback["satisfied"],
                "dissatisfied": feedback["dissatisfied"],
                "total": feedback["total"],
                "satisfaction_rate": feedback["satisfaction_rate"],
            },
            "dissatisfied_tickets": feedback["dissatisfied_tickets"],
        },
    }


@router.get("/tickets/{ticket_id}/trace")
async def get_ticket_trace(
    ticket_id: str,
    request: Request,
    _role_check: dict = Depends(require_role("developer")),
) -> dict:
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
    _role_check: dict = Depends(require_role("developer")),
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
async def get_trace_stats(
    trace_id: str,
    request: Request,
    _role_check: dict = Depends(require_role("developer")),
) -> dict:
    """获取 trace 耗时分析。"""
    db_manager = request.app.state.db_manager
    stats = await db_manager.get_trace_stats(trace_id)
    if stats is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Trace not found"})
    return stats


@router.get("/traces/{trace_id}/decisions")
async def get_trace_decisions(
    trace_id: str,
    request: Request,
    _role_check: dict = Depends(require_role("developer")),
) -> dict:
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
    # 用户隔离校验：accept 之前先查工单 + 校验访问权限
    db_manager = websocket.app.state.db_manager
    ticket = await db_manager.get_ticket(ticket_id)
    if ticket is None:
        await websocket.close(code=4404)
        return
    try:
        from src.multi_agent_system.core.auth import assert_ticket_access
        assert_ticket_access(ticket, websocket)
    except HTTPException:
        # 4403 自定义关闭码：无权访问
        await websocket.close(code=4403)
        return

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
