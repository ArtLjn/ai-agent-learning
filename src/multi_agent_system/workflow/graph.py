"""LangGraph 工单处理状态机定义。

使用 StateGraph 构建完整的工单处理流程，包含分类、路由、处理、审核、
重试等节点。支持 Agent 注入模式：传入 agents 时使用 LLM Agent，
否则使用占位实现（向后兼容）。
"""

import re
from typing import TYPE_CHECKING, Any, Literal

from langgraph.graph import END, START, StateGraph
from loguru import logger

from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.logging import generate_trace_id, log_context, trace_id_var
from src.multi_agent_system.core.risk_policy import assess_ticket_risk
from src.multi_agent_system.models.ticket import TicketCategory, TicketPriority
from src.multi_agent_system.workflow.state import TicketState

if TYPE_CHECKING:
    from src.multi_agent_system.agents.classifier import ClassifierAgent
    from src.multi_agent_system.agents.coordinator import CoordinatorAgent
    from src.multi_agent_system.agents.processor import ProcessorAgent
    from src.multi_agent_system.agents.reviewer import ReviewerAgent
    from src.multi_agent_system.core.database import DatabaseManager
    from src.multi_agent_system.core.trace import TraceManager

__all__ = [
    "apply_human_decision",
    "build_ticket_graph",
    "create_initial_state",
    "human_decision_router",
    "human_review_wait",
    "request_user_input",
    "pause_for_user_input",
    "finalize_knowledge_gap",
    "resume_from_human_decision",
    "resume_from_user_input",
]


def _get_settings() -> Settings:
    """获取配置单例。"""
    return Settings()


# 占位分类关键词映射：关键词 -> (分类, 优先级)
_CLASSIFY_RULES: dict[str, tuple[str, str]] = {
    "崩溃": (TicketCategory.TECHNICAL.value, TicketPriority.P1.value),
    "报错": (TicketCategory.TECHNICAL.value, TicketPriority.P2.value),
    "无法登录": (TicketCategory.TECHNICAL.value, TicketPriority.P1.value),
    "退款": (TicketCategory.BILLING.value, TicketPriority.P2.value),
    "账单": (TicketCategory.BILLING.value, TicketPriority.P2.value),
    "扣费": (TicketCategory.BILLING.value, TicketPriority.P1.value),
    "投诉": (TicketCategory.COMPLAINT.value, TicketPriority.P1.value),
    "不满": (TicketCategory.COMPLAINT.value, TicketPriority.P1.value),
    "咨询": (TicketCategory.INQUIRY.value, TicketPriority.P3.value),
    "如何": (TicketCategory.INQUIRY.value, TicketPriority.P3.value),
    "怎么": (TicketCategory.INQUIRY.value, TicketPriority.P3.value),
}


def create_initial_state(content: str, ticket_id: str | None = None) -> TicketState:
    """创建工单初始状态。

    Args:
        content: 工单内容
        ticket_id: 工单ID，不传时自动生成

    Returns:
        初始化后的工单状态字典
    """
    if ticket_id is None:
        ticket_id = f"TK-{generate_trace_id()}"

    # 绑定 trace_id 到日志上下文（贯穿整个工作流生命周期）
    trace_id_var.set(ticket_id)

    return TicketState(
        ticket_id=ticket_id,
        content=content,
        category=None,
        priority=None,
        processing_result=None,
        references=[],
        review_score=None,
        review_should_retry=None,
        review_retry_suppressed=None,
        review_issue_type=None,
        clarification_request=None,
        auto_finalized_review_failure=None,
        retry_count=0,
        status="received",
        messages=[],
        error=None,
        risk_level=None,
        requires_human_review=None,
        risk_reason=None,
    )


# ============================================================
# 节点函数
# ============================================================

# 模块级 Agent 引用（由 build_ticket_graph 注入）
_classifier_agent: "ClassifierAgent | None" = None
_processor_agent: "ProcessorAgent | None" = None
_reviewer_agent: "ReviewerAgent | None" = None
_coordinator_agent: "CoordinatorAgent | None" = None

# 模块级 MemoryManager 引用（由 lifespan 注入）
_memory_manager = None

# 模块级 TraceManager 引用（由 lifespan 注入）
_trace_manager = None

# 模块级 DatabaseManager 引用（人工审核节点持久化使用）
_db_manager: "DatabaseManager | None" = None

# 模块级活跃 trace_id（用于跨 task 传播，绕过 contextvar 限制）
_active_trace_id: str | None = None


class _NoOpSpanContext:
    """无活跃 trace 时的空操作 span。"""

    span_id = ""
    trace_id = ""

    def set_output(self, data: dict[str, Any]) -> None:
        pass

    def set_metadata(self, data: dict[str, Any]) -> None:
        pass

    def set_status(self, status: str) -> None:
        pass

    async def __aenter__(self) -> "_NoOpSpanContext":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


def _restore_trace_context(state: TicketState) -> None:
    """从 state 中恢复 trace context（用于后台 task 跨节点传播）。"""
    global _active_trace_id  # noqa: PLW0603
    trace_id = state.get("__trace_id__")
    logger.debug(f"[_restore_trace_context] trace_id from state={trace_id}, current _active_trace_id={_active_trace_id}")
    if trace_id is not None:
        from src.multi_agent_system.core.trace import current_trace_id
        current_trace_id.set(trace_id)
        _active_trace_id = trace_id
        logger.debug(f"[_restore_trace_context] set _active_trace_id={trace_id}")


def _span(name: str, span_type: str = "node", **kwargs: Any):
    """获取当前 span context manager，无 TraceManager 时返回 no-op。

    优先使用模块级 _active_trace_id（跨 task 兼容），
    其次使用 contextvar current_trace_id。
    """
    if _trace_manager is None:
        logger.debug(f"[_span] {name}: _trace_manager is None")
        return _NoOpSpanContext()

    # 优先使用模块级 trace_id（跨 task 传播）
    trace_id = _active_trace_id
    if trace_id is None:
        from src.multi_agent_system.core.trace import current_trace_id
        trace_id = current_trace_id.get()

    if trace_id is None:
        logger.debug(f"[_span] {name}: trace_id is None")
        return _NoOpSpanContext()

    logger.debug(f"[_span] {name}: using trace_id={trace_id}")
    return _trace_manager.start_span(
        name=name,
        span_type=span_type,
        trace_id=trace_id,
        **kwargs,
    )


def _build_classify_decision(
    content: str,
    category: str,
    confidence: float | None,
    reason: str,
) -> dict[str, Any]:
    """构造 classify 节点的 routing 决策记录。"""
    options = [
        {"value": c.value, "score": 0.0, "reason": "未选中"}
        for c in TicketCategory
    ]
    for opt in options:
        if opt["value"] == category:
            opt["score"] = confidence if confidence is not None else 1.0
            opt["reason"] = reason or "LLM 选择"
    return {
        "decision_type": "routing",
        "trigger": {"content_preview": content[:200]},
        "options": options,
        "selection": {
            "value": category,
            "confidence": confidence if confidence is not None else 1.0,
            "reason": reason or "LLM 选择",
        },
        "execution": {"downstream_node": "route"},
    }


def _build_review_decision(
    score: float,
    threshold: float,
    *,
    retry_suppressed: bool = False,
    issue_type: str | None = None,
    has_conversation_context: bool = False,
) -> dict[str, Any]:
    """构造 review 节点的 quality_gate 决策记录。"""
    passed = score >= threshold or retry_suppressed
    confidence = min(1.0, abs(score - threshold) / max(threshold, 1e-6))
    finalize_gap = retry_suppressed and _should_finalize_without_user_input(
        issue_type,
        has_conversation_context=has_conversation_context,
    )
    if finalize_gap:
        selection = "finalize_knowledge_gap"
    elif retry_suppressed:
        selection = "request_user_input"
    else:
        selection = "pass" if passed else "reject_for_rework"
    return {
        "decision_type": "quality_gate",
        "trigger": {
            "review_score": round(score, 4),
            "threshold": threshold,
            "issue_type": issue_type,
            "retry_suppressed": retry_suppressed,
            "has_conversation_context": has_conversation_context,
        },
        "options": [
            {"value": "pass", "score": round(score, 4), "reason": f"score >= {threshold}"},
            {"value": "reject_for_rework", "score": round(1 - score, 4), "reason": f"score < {threshold}"},
            {
                "value": "request_user_input",
                "score": 1.0 if retry_suppressed and not finalize_gap else 0.0,
                "reason": "用户描述信息不足，需要补充上下文",
            },
            {
                "value": "finalize_knowledge_gap",
                "score": 1.0 if finalize_gap else 0.0,
                "reason": "知识库缺失或用户已补充后仍无可靠知识，停止追问并归档",
            },
        ],
        "selection": {
            "value": selection,
            "confidence": round(confidence, 4),
            "reason": (
                "无可靠知识可用，归档暂无答案"
                if finalize_gap
                else ("信息不足，等待用户补充" if retry_suppressed else "阈值判断")
            ),
        },
    }


def _should_finalize_without_user_input(
    issue_type: str | None,
    *,
    has_conversation_context: bool = False,
) -> bool:
    """判断不可重试问题是否应直接归档，而不是继续要求用户补充。"""
    if has_conversation_context:
        return True
    return issue_type in {"knowledge_gap", "out_of_scope"}


def _should_block_for_rework(
    *,
    score: float,
    threshold: float,
    should_retry: bool,
    retry_suppressed: bool,
    has_blocking_artifact: bool = False,
) -> bool:
    """判断 Reviewer 结果是否真的要阻断流程返工。"""
    if retry_suppressed:
        return False
    if has_blocking_artifact:
        return True
    if score < threshold:
        return True
    if should_retry and score < min(0.8, threshold + 0.1):
        return True
    return False


def _has_blocking_artifact(*values: object) -> bool:
    """识别示例域名、占位变量等不应出现在最终回复里的幻觉痕迹。"""
    text = "\n".join(str(value or "") for value in values).lower()
    blocking_markers = (
        "example.com",
        "api.example",
        "cloud.example",
        "target-domain",
        "<user_id>",
        "<order_id>",
        "<api_key>",
        "占位地址",
        "示例域名",
        "占位 url",
        "占位url",
    )
    return any(marker in text for marker in blocking_markers)


def _format_review_feedback(
    result: dict[str, Any],
    threshold: float,
    *,
    has_conversation_context: bool = False,
) -> str:
    """格式化 Reviewer 的结构化质检结论，写入 Agent 消息链。"""
    score = float(result.get("score", 0.0))
    dimensions = result.get("dimensions") if isinstance(result.get("dimensions"), dict) else {}
    labels = {
        "accuracy": "准确性",
        "feasibility": "可行性",
        "completeness": "完整性",
        "professionalism": "专业性",
    }
    dimension_text = " / ".join(
        f"{label}: {float(dimensions.get(key, 0.0)):.2f}"
        for key, label in labels.items()
    )
    issues = result.get("issues") if isinstance(result.get("issues"), list) else []
    issue_text = "；".join(str(issue) for issue in issues if str(issue).strip()) or "未发现阻断问题"
    suggestion = str(result.get("suggestion") or result.get("feedback") or "保持当前处理结果")
    retry_suppressed = bool(result.get("retry_suppressed"))
    should_retry = _should_block_for_rework(
        score=score,
        threshold=threshold,
        should_retry=bool(result.get("should_retry")),
        retry_suppressed=retry_suppressed,
        has_blocking_artifact=_has_blocking_artifact(
            result.get("issues"),
            result.get("suggestion"),
            result.get("feedback"),
        ),
    )
    issue_type = str(result.get("issue_type") or "none")
    finalize_gap = retry_suppressed and _should_finalize_without_user_input(
        issue_type,
        has_conversation_context=has_conversation_context,
    )
    if finalize_gap:
        gate = "归档暂无答案"
    elif retry_suppressed:
        gate = "等待用户补充"
    else:
        gate = "打回返工" if should_retry else "通过"
    return (
        f"审核评分: {score:.2f}（阈值 {threshold:.2f}，{gate}）\n"
        f"质检维度: {dimension_text}\n"
        f"发现问题: {issue_text}\n"
        f"建议: {suggestion}"
    )


def _build_agent_handoff(
    *,
    from_agent: str,
    to_agent: str,
    artifact: str,
    summary: str,
) -> dict[str, str]:
    """构造多 Agent 协作交接信息，供 trace 前端展示。"""
    return {
        "from_agent": from_agent,
        "to_agent": to_agent,
        "artifact": artifact,
        "summary": summary[:240],
    }


async def receive(state: TicketState) -> dict:
    """初始化工单状态，加载用户长期记忆。"""
    global _active_trace_id  # noqa: PLW0603
    with log_context(agent="receive"):
        # 启动 trace，并将 trace_id 写回 state 供后续节点恢复 context
        trace_id = None
        if _trace_manager is not None:
            trace_id = await _trace_manager.start_trace(state["ticket_id"])
            from src.multi_agent_system.core.trace import current_trace_id

            current_trace_id.set(trace_id)
            _active_trace_id = trace_id

        # 恢复 trace context（从 state 中读取，兼容后台 task 场景）
        _restore_trace_context(state)

        async with _span("receive", input_data={"content": state["content"]}) as span:
            # Load user context if user_id present
            user_context = {}
            if _memory_manager and state.get("user_id"):
                async with _span(
                    "load_user_context",
                    span_type="memory_call",
                    input_data={"user_id": state["user_id"]},
                ) as mem_span:
                    user_context = await _memory_manager.load_user_context(state["user_id"])
                    await _memory_manager.ensure_user(state["user_id"])
                    mem_span.set_output({"context_keys": list(user_context.keys())})

            result = {
                "status": "received",
                "user_context": user_context,
                "messages": state.get("messages", [])
                + [{"role": "system", "content": f"工单 {state['ticket_id']} 已接收"}],
                "__trace_id__": trace_id,
            }
            span.set_output({"status": "received"})
            return result


async def classify(state: TicketState) -> dict:
    """分类节点：优先使用 ClassifierAgent，不可用时降级到关键词匹配。

    Agent 内部的重试和降级由 @with_retry 装饰器统一处理，
    此处仅保留 Agent 实例不可用时的占位实现。
    """
    _restore_trace_context(state)
    with log_context(agent="classifier"):
        async with _span("classify", input_data={"content": state["content"]}) as span:
            content = state["content"]

            # Agent 可用时，直接调用（重试/降级由装饰器处理）
            if _classifier_agent is not None:
                result = await _classifier_agent.classify(content)
                category = result["category"]
                priority = result["priority"]
                reason = result.get("reason", "")
                confidence = result.get("confidence")
                existing_requires_review = bool(state.get("requires_human_review"))
                existing_risk_reason = state.get("risk_reason")
                requires_human_review = (
                    existing_requires_review
                    or bool(result.get("requires_human_review"))
                )
                risk_reason = (
                    existing_risk_reason
                    if existing_requires_review
                    else result.get("risk_reason")
                )
                risk_level = (
                    state.get("risk_level")
                    if existing_requires_review
                    else result.get("risk_level")
                )
                risk = assess_ticket_risk(
                    content,
                    category=category,
                    priority=priority,
                    agent_risk={
                        **result,
                        "risk_level": risk_level,
                        "requires_human_review": requires_human_review,
                        "risk_reason": risk_reason,
                    },
                )
                span.set_output({"category": category, "priority": priority})
                span.set_metadata({"decision": _build_classify_decision(
                    content=content,
                    category=category,
                    confidence=confidence,
                    reason=reason,
                )})
                return {
                    "category": category,
                    "priority": priority,
                    "risk_level": risk.risk_level,
                    "requires_human_review": risk.requires_human_review,
                    "risk_reason": risk.reason,
                    "status": "classifying",
                    "messages": state["messages"]
                    + [
                        {
                            "role": "classifier",
                            "content": f"分类结果: {category}, 优先级: {priority}, 理由: {reason}",
                        }
                    ],
                }

            # 占位分类：关键词匹配
            for keyword, (category, priority) in _CLASSIFY_RULES.items():
                if keyword in content:
                    risk = assess_ticket_risk(
                        content,
                        category=category,
                        priority=priority,
                    )
                    span.set_output({"category": category, "priority": priority})
                    return {
                        "category": category,
                        "priority": priority,
                        "risk_level": risk.risk_level,
                        "requires_human_review": risk.requires_human_review,
                        "risk_reason": risk.reason,
                        "status": "classifying",
                        "messages": state["messages"]
                        + [
                            {
                                "role": "classifier",
                                "content": f"分类结果: {category}, 优先级: {priority}",
                            }
                        ],
                    }

            # 默认分类为咨询，优先级 P3
            risk = assess_ticket_risk(
                content,
                category=TicketCategory.INQUIRY.value,
                priority=TicketPriority.P3.value,
            )
            if risk.requires_human_review:
                category = TicketCategory.TECHNICAL.value
                priority = TicketPriority.P1.value
            else:
                category = TicketCategory.INQUIRY.value
                priority = TicketPriority.P3.value
            span.set_output({"category": category, "priority": priority})
            return {
                "category": category,
                "priority": priority,
                "risk_level": risk.risk_level,
                "requires_human_review": risk.requires_human_review,
                "risk_reason": risk.reason,
                "status": "classifying",
                "messages": state["messages"]
                + [
                    {
                        "role": "classifier",
                        "content": f"分类结果: {category}, 优先级: {priority}（默认）",
                    }
                ],
            }


async def route(state: TicketState) -> dict:
    """路由节点：根据分类和优先级决定后续路径。

    本节点不做状态修改，仅作为条件路由的汇聚点。
    """
    _restore_trace_context(state)
    with log_context(agent="router"):
        return {}


async def process(state: TicketState) -> dict:
    """处理节点：优先使用 ProcessorAgent，不可用时降级到占位实现。

    Agent 内部的重试和降级由 @with_retry 装饰器统一处理，
    此处仅保留 Agent 实例不可用时的占位实现。
    """
    _restore_trace_context(state)
    with log_context(agent="processor"):
        async with _span("process", input_data={"category": state.get("category"), "priority": state.get("priority")}) as span:
            category = state.get("category", "")
            priority = state.get("priority", "P3")
            content = state["content"]
            conversation_context = state.get("conversation_context")
            if conversation_context:
                content = (
                    f"{content}\n\n补充信息记录：\n{conversation_context}\n"
                    "请结合原始工单和补充信息处理。"
                )

            # Agent 可用时，直接调用（重试/降级由装饰器处理）
            if _processor_agent is not None:
                result = await _processor_agent.process(content, category, priority)
                processing_result = result["result"]
                references = result.get("references", [])
                span.set_output(
                    {
                        "result_length": len(processing_result),
                        "reference_count": len(references),
                    }
                )
                span.set_metadata({
                    "agent_handoff": _build_agent_handoff(
                        from_agent="Processor Agent",
                        to_agent="Reviewer Agent",
                        artifact="处理方案草稿",
                        summary=processing_result,
                    ),
                })
                return {
                    "processing_result": processing_result,
                    "references": references,
                    "status": "processing",
                    "messages": state["messages"]
                    + [{"role": "processor", "content": processing_result}],
                }

            # 占位处理：根据分类生成模拟结果
            result_map = {
                TicketCategory.TECHNICAL.value: f"已排查技术问题，生成解决方案（优先级: {priority}）",
                TicketCategory.BILLING.value: f"已核实账单信息，生成处理方案（优先级: {priority}）",
            }
            processing_result = result_map.get(
                category, f"已处理工单（分类: {category}, 优先级: {priority}）"
            )
            span.set_output({"result": "placeholder"})
            span.set_metadata({
                "agent_handoff": _build_agent_handoff(
                    from_agent="Processor Agent",
                    to_agent="Reviewer Agent",
                    artifact="占位处理方案",
                    summary=processing_result,
                ),
            })

            return {
                "processing_result": processing_result,
                "status": "processing",
                "messages": state["messages"]
                + [{"role": "processor", "content": processing_result}],
            }


async def review(state: TicketState) -> dict:
    """审核节点：优先使用 ReviewerAgent，不可用时降级到占位评分。

    Agent 内部的重试和降级由 @with_retry 装饰器统一处理，
    此处仅保留 Agent 实例不可用时的占位实现。
    """
    _restore_trace_context(state)
    with log_context(agent="reviewer"):
        async with _span("review", input_data={"retry_count": state.get("retry_count", 0)}) as span:
            retry_count = state.get("retry_count", 0)
            content = state["content"]
            processing_result = state.get("processing_result", "")
            category = state.get("category", "")

            # Agent 可用时，直接调用（重试/降级由装饰器处理）
            if _reviewer_agent is not None:
                result = await _reviewer_agent.review(content, processing_result, category)
                score = result["score"]
                threshold = _get_settings().review_threshold
                retry_suppressed = bool(result.get("retry_suppressed"))
                raw_should_retry = bool(result.get("should_retry", score < threshold))
                has_blocking_artifact = _has_blocking_artifact(
                    processing_result,
                    result.get("issues"),
                    result.get("suggestion"),
                    result.get("feedback"),
                )
                should_retry = _should_block_for_rework(
                    score=score,
                    threshold=threshold,
                    should_retry=raw_should_retry,
                    retry_suppressed=retry_suppressed,
                    has_blocking_artifact=has_blocking_artifact,
                )
                issue_type = str(result.get("issue_type") or "none")
                clarification_request = str(result.get("clarification_request") or "")
                span.set_output({
                    "review_score": score,
                    "feedback": result.get("feedback", ""),
                    "dimensions": result.get("dimensions", {}),
                    "issues": result.get("issues", []),
                    "suggestion": result.get("suggestion", ""),
                    "should_retry": should_retry,
                    "raw_should_retry": raw_should_retry,
                    "has_blocking_artifact": has_blocking_artifact,
                    "issue_type": issue_type,
                    "retry_suppressed": retry_suppressed,
                    "clarification_request": clarification_request,
                })
                span.set_metadata({
                    "decision": _build_review_decision(
                        score=score,
                        threshold=threshold,
                        retry_suppressed=retry_suppressed,
                        issue_type=issue_type,
                        has_conversation_context=bool(state.get("conversation_context")),
                    ),
                    "agent_handoff": _build_agent_handoff(
                        from_agent="Reviewer Agent",
                        to_agent=(
                            "Knowledge Gap Finalizer"
                            if retry_suppressed and _should_finalize_without_user_input(
                                issue_type,
                                has_conversation_context=bool(state.get("conversation_context")),
                            )
                            else "User Input Gate"
                            if retry_suppressed
                            else ("Notification Agent" if score >= threshold else "Processor Agent")
                        ),
                        artifact="质量门禁结论",
                        summary=clarification_request or result.get("suggestion") or result.get("feedback") or "",
                    ),
                })
                return {
                    "review_score": score,
                    "review_should_retry": should_retry,
                    "review_retry_suppressed": retry_suppressed,
                    "review_issue_type": issue_type,
                    "clarification_request": clarification_request,
                    "review_feedback": str(result.get("feedback") or ""),
                    "review_issues": result.get("issues", []),
                    "review_suggestion": str(result.get("suggestion") or ""),
                    "status": "reviewing",
                    "messages": state["messages"]
                    + [
                        {
                            "role": "reviewer",
                            "content": _format_review_feedback(
                                result,
                                threshold,
                                has_conversation_context=bool(state.get("conversation_context")),
                            ),
                        }
                    ],
                }

            # 占位审核：重试次数越多，评分越低
            base_score = 0.85
            score = max(0.3, base_score - retry_count * 0.15)
            span.set_output({"review_score": score})
            span.set_metadata({
                "decision": _build_review_decision(
                    score=score,
                    threshold=_get_settings().review_threshold,
                ),
                "agent_handoff": _build_agent_handoff(
                    from_agent="Reviewer Agent",
                    to_agent="Notification Agent" if score >= _get_settings().review_threshold else "Processor Agent",
                    artifact="占位质量门禁结论",
                    summary=f"占位审核评分 {score:.2f}",
                ),
            })

            return {
                "review_score": score,
                "review_should_retry": score < _get_settings().review_threshold,
                "status": "reviewing",
                "messages": state["messages"]
                + [{"role": "reviewer", "content": f"审核评分: {score:.2f}"}],
            }


async def auto_reply(state: TicketState) -> dict:
    """咨询类工单直接生成回复。"""
    _restore_trace_context(state)
    with log_context(agent="auto_reply"):
        async with _span("auto_reply", input_data={"category": state.get("category")}) as span:
            reply = f"感谢您的咨询。关于「{state['content'][:50]}」，已为您生成自动回复。"
            span.set_output({"reply_length": len(reply)})
            return {
                "processing_result": reply,
                "status": "processing",
                "messages": state["messages"] + [{"role": "auto_reply", "content": reply}],
            }


async def escalate(state: TicketState) -> dict:
    """升级节点：P0 或投诉类工单标记需要人工处理。"""
    _restore_trace_context(state)
    with log_context(agent="escalation"):
        async with _span("escalate", input_data={"category": state.get("category"), "priority": state.get("priority")}) as span:
            category = state.get("category", "")
            priority = state.get("priority", "P3")
            content = state.get("content", "")
            risk = assess_ticket_risk(
                content,
                category=category,
                priority=priority,
                agent_risk={
                    "risk_level": state.get("risk_level"),
                    "requires_human_review": state.get("requires_human_review"),
                    "risk_reason": state.get("risk_reason"),
                },
            )

            if risk.requires_human_review:
                reason = risk.reason
            elif priority == TicketPriority.P0.value:
                reason = "P0 紧急工单"
            else:
                reason = f"投诉类工单（分类: {category}）"
            escalation_msg = f"已升级至人工处理，原因: {reason}"
            span.set_output({"reason": reason})

            return {
                "processing_result": escalation_msg,
                "status": "processing",
                "trigger_type": state.get("trigger_type") or "escalate",
                "trigger_reason": state.get("trigger_reason") or reason,
                "messages": state["messages"]
                + [{"role": "escalator", "content": escalation_msg}],
            }


async def notify(state: TicketState) -> dict:
    """发送处理结果通知。"""
    _restore_trace_context(state)
    with log_context(agent="notification"):
        async with _span("notify", input_data={"has_result": bool(state.get("processing_result"))}) as span:
            result = state.get("processing_result", "无处理结果")
            notification = f"通知: 工单 {state['ticket_id']} 处理完成 - {result}"
            span.set_output({"notification_length": len(notification)})

            return {
                "messages": state["messages"] + [{"role": "notifier", "content": notification}],
            }


async def request_user_input(state: TicketState) -> dict:
    """知识盲区或信息不足时暂停工作流，等待用户补充信息。"""
    _restore_trace_context(state)
    with log_context(agent="request_user_input"):
        issue_type = state.get("review_issue_type") or "needs_clarification"
        request_text = (
            state.get("clarification_request")
            or "当前信息不足，暂时无法生成可靠处理方案，请补充更具体的业务场景或操作现象。"
        )
        result_text = _build_user_input_processing_result(issue_type, request_text)

        if _db_manager is not None:
            existing_messages = await _db_manager.list_ticket_messages(state["ticket_id"])
            already_created = any(
                msg.get("metadata", {}).get("source") == "agent_clarification_request"
                for msg in existing_messages
            )
            if not already_created:
                await _db_manager.create_ticket_message({
                    "message_id": f"TM-{generate_trace_id()}",
                    "ticket_id": state["ticket_id"],
                    "sender_type": "reviewer",
                    "sender_id": "reviewer-agent",
                    "content": request_text,
                    "metadata": {
                        "source": "agent_clarification_request",
                        "issue_type": issue_type,
                    },
                })

        async with _span(
            "request_user_input",
            input_data={
                "issue_type": issue_type,
                "review_score": state.get("review_score"),
            },
        ) as span:
            span.set_output({
                "status": "waiting_user_input",
                "issue_type": issue_type,
                "clarification_request": request_text,
            })

        return {
            "processing_result": result_text,
            "status": "waiting_user_input",
            "messages": state["messages"]
            + [
                {
                    "role": "system",
                    "content": f"等待用户补充信息：{request_text}",
                }
            ],
        }


def _build_user_input_processing_result(issue_type: str, request_text: str) -> str:
    """生成暂停等待补充时的用户可读处理结果。"""
    title_map = {
        "knowledge_gap": "当前知识库未覆盖该问题的可靠处理方案",
        "needs_clarification": "当前问题描述还需要补充关键信息",
        "out_of_scope": "当前问题可能超出自动处理范围",
    }
    title = title_map.get(issue_type, "当前需要补充信息后继续处理")
    return (
        f"{title}。\n\n"
        f"{request_text}\n\n"
        "补充后 Agent 会结合新信息重新检索知识库并继续处理，不会再进行无效重复重试。"
    )


async def finalize_knowledge_gap(state: TicketState) -> dict:
    """知识盲区收敛节点：无命中给暂无答案，有相关命中给参考答复并归档。"""
    _restore_trace_context(state)
    with log_context(agent="knowledge_gap_finalizer"):
        result_text = _build_final_knowledge_gap_result(state)
        async with _span(
            "finalize_knowledge_gap",
            input_data={
                "issue_type": state.get("review_issue_type"),
                "has_conversation_context": bool(state.get("conversation_context")),
            },
        ) as span:
            span.set_output({
                "status": "processing",
                "result_length": len(result_text),
            })

        return {
            "processing_result": result_text,
            "review_should_retry": False,
            "review_retry_suppressed": False,
            "clarification_request": None,
            "status": "processing",
            "messages": state["messages"]
            + [
                {
                    "role": "system",
                    "content": "知识盲区已收敛处理，已停止重复追问并生成最终答复。",
                }
            ],
        }


def _build_final_knowledge_gap_result(state: TicketState) -> str:
    """生成知识盲区最终答复；若有相关命中，保留参考价值而不纯说不知道。"""
    content = str(state.get("content") or "").strip()
    feedback = str(state.get("review_feedback") or "").strip()
    issue_text = _join_text_list(state.get("review_issues"))
    context = str(state.get("conversation_context") or "").strip()
    reference_text = _summarize_references(state.get("references"), max_length=520)

    if reference_text:
        lines = [
            "您好，已根据现有资料整理出一组可先核对的方向。",
            "",
            f"可参考的资料要点：{reference_text}",
            "",
            "可以先按以下方向核对：",
            "1. 先确认本次咨询的产品/平台、账号权限、应用类型和业务场景是否与知识库片段一致。",
            "2. 对接或配置类问题，重点核对 Key/Secret、应用标识、白名单、服务开通状态和接口返回码。",
            "3. 流程或规则类问题，重点核对适用账号范围、入口路径、审批要求和最新业务规则。",
            "",
            "如仍无法确认，请补充具体平台入口、账号权限、截图或内部规则说明，我们会继续核对。",
        ]
        if content:
            lines.extend(["", f"本次咨询：{content[:160]}"])
        if context:
            lines.extend(["", f"已收到补充：{context[-180:]}"])
        if issue_text or feedback:
            lines.extend(["", f"审核结论：{issue_text or feedback}"])
        return "\n".join(lines)

    lines = [
        "您好，知识库暂时没有收录该问题的明确答案。",
        "",
        "当前知识库无法确认具体平台入口、报销路径或操作规则，因此不会继续猜测平台名称、URL 或流程。",
    ]
    if content:
        lines.extend(["", f"本次咨询：{content[:160]}"])
    if context:
        lines.extend(["", f"已收到补充：{context[-180:]}"])
    if issue_text or feedback:
        lines.extend(["", f"审核结论：{issue_text or feedback}"])
    lines.extend([
        "",
        "建议后续将对应业务入口、适用账号范围、平台路径和处理规则补充进知识库后，再由 Agent 自动回答。",
    ])
    return "\n".join(lines)


async def complete(state: TicketState) -> dict:
    """归档节点：标记工单状态为已完成。"""
    _restore_trace_context(state)
    with log_context(agent="complete"):
        async with _span("complete") as span:
            if _trace_manager is not None:
                from src.multi_agent_system.core.trace import current_trace_id

                tid = current_trace_id.get()
                if tid:
                    await _trace_manager.finish_trace(tid, "completed")
            span.set_output({"status": "completed"})
            return {
                "status": "completed",
                "messages": state["messages"]
                + [{"role": "system", "content": f"工单 {state['ticket_id']} 已归档完成"}],
            }


async def handle_failure(state: TicketState) -> dict:
    """失败处理节点：标记工单状态为失败。"""
    _restore_trace_context(state)
    with log_context(agent="failure_handler"):
        async with _span("handle_failure", input_data={"error": state.get("error")}) as span:
            error_msg = state.get("error", f"工单处理失败，已达最大重试次数({_get_settings().max_retries}次)")
            if _trace_manager is not None:
                from src.multi_agent_system.core.trace import current_trace_id

                tid = current_trace_id.get()
                if tid:
                    await _trace_manager.finish_trace(tid, "failed", error=error_msg)
            span.set_output({"status": "failed"})
            return {
                "status": "failed",
                "error": error_msg,
                "messages": state["messages"] + [{"role": "system", "content": error_msg}],
            }


# ============================================================
# 条件边函数
# ============================================================


def route_decision(state: TicketState) -> Literal["auto_reply", "escalate", "process"]:
    """路由决策：根据分类和优先级选择处理路径。

    - 安全/漏洞风险 -> escalate
    - 投诉类 -> escalate
    - P0 优先级 -> escalate
    - 其他（含咨询）-> process
    """
    category = state.get("category", "")
    priority = state.get("priority", "P3")
    content = state.get("content", "")
    risk = assess_ticket_risk(
        content,
        category=category,
        priority=priority,
        agent_risk={
            "risk_level": state.get("risk_level"),
            "requires_human_review": state.get("requires_human_review"),
            "risk_reason": state.get("risk_reason"),
        },
    )

    if risk.requires_human_review:
        return "escalate"
    if category == TicketCategory.COMPLAINT.value:
        return "escalate"
    if priority == TicketPriority.P0.value:
        return "escalate"
    return "process"


def review_decision(
    state: TicketState,
) -> Literal["notify", "retry_check", "request_user_input", "finalize_knowledge_gap"]:
    """审核决策：根据评分决定是否通过。

    - 知识库缺失/范围外 -> finalize_knowledge_gap（停止追问并归档暂无答案）
    - 用户描述信息不足 -> request_user_input（等待用户补充）
    - score >= 阈值 -> notify（通过）
    - score < 阈值 -> retry_check（重试检查）
    """
    score = state.get("review_score", 0.0)
    if state.get("review_retry_suppressed"):
        if _should_finalize_without_user_input(
            state.get("review_issue_type"),
            has_conversation_context=bool(state.get("conversation_context")),
        ):
            return "finalize_knowledge_gap"
        return "request_user_input"
    if state.get("review_should_retry"):
        return "retry_check"
    if score >= _get_settings().review_threshold:
        return "notify"
    return "retry_check"


def retry_decision(state: TicketState) -> Literal["process", "human_review_wait", "notify"]:
    """重试决策：检查重试次数是否已达上限。

    - auto_finalized_review_failure -> notify（低风险咨询安全兜底完成）
    - retry_count < max_retries -> process（重试）
    - retry_count >= max_retries -> human_review_wait（转人工审核）
    """
    if state.get("auto_finalized_review_failure"):
        return "notify"
    retry_count = state.get("retry_count", 0)
    if retry_count < _get_settings().max_retries:
        return "process"
    return "human_review_wait"


# ============================================================
# retry_check 节点（递增 retry_count 后进入条件边）
# ============================================================


async def retry_check(state: TicketState) -> dict:
    """重试检查节点：递增重试计数，并记录决策点。

    若低风险咨询出现可修复但反复影响演示的质检问题，生成安全兜底答复并归档；
    若 retry_count 已达上限（即将进入 human_review_wait），
    预置 trigger_type=review_failed 供人工审核节点使用。
    """
    _restore_trace_context(state)
    with log_context(agent="retry_check"):
        new_retry = state.get("retry_count", 0) + 1
        max_retries = _get_settings().max_retries
        auto_finalize = _should_auto_finalize_review_failure(state)
        will_escalate = not auto_finalize and new_retry >= max_retries
        result: dict[str, Any] = {
            "retry_count": new_retry,
            "review_score": None,  # 重置评分，等待重新审核
        }
        if auto_finalize:
            safe_result = _build_auto_finalized_result(state)
            result.update({
                "processing_result": safe_result,
                "review_score": max(_get_settings().review_threshold, 0.72),
                "review_should_retry": False,
                "auto_finalized_review_failure": True,
                "status": "processing",
                "messages": state["messages"]
                + [{
                    "role": "system",
                    "content": "低风险咨询质检未通过，已生成演示安全版答复并停止重复返工。",
                }],
            })
        if will_escalate:
            result["trigger_type"] = state.get("trigger_type") or "review_failed"
            result["trigger_reason"] = (
                state.get("trigger_reason")
                or f"AI 多次审核未通过（retry_count={new_retry}）"
            )

        # 决策点埋点：retry vs escalate 的边界判断
        async with _span(
            "retry_check",
            input_data={
                "retry_count": new_retry,
                "max_retries": max_retries,
                "auto_finalize": auto_finalize,
            },
        ) as span:
            span.set_output({
                "will_escalate": will_escalate,
                "auto_finalized": auto_finalize,
            })
            span.set_metadata({"decision": {
                "decision_type": "boundary",
                "trigger": {
                    "retry_count": new_retry,
                    "max_retries": max_retries,
                    "review_score": state.get("review_score"),
                    "auto_finalize": auto_finalize,
                },
                "options": [
                    {
                        "value": "auto_finalize",
                        "score": 1.0 if auto_finalize else 0.0,
                        "reason": "低风险咨询质检失败，生成安全答复并归档",
                    },
                    {
                        "value": "retry",
                        "score": 0.0 if will_escalate or auto_finalize else 1.0,
                        "reason": f"retry_count={new_retry} < max={max_retries}",
                    },
                    {
                        "value": "escalate",
                        "score": 1.0 if will_escalate else 0.0,
                        "reason": f"retry_count={new_retry} >= max={max_retries}",
                    },
                ],
                "selection": {
                    "value": "auto_finalize" if auto_finalize else ("escalate" if will_escalate else "retry"),
                    "confidence": 1.0,
                    "reason": "演示安全兜底" if auto_finalize else "硬阈值",
                },
            }})
        return result


def _should_auto_finalize_review_failure(state: TicketState) -> bool:
    """判断质检失败是否可用安全答复自动闭环，避免演示频繁转人工。"""
    if state.get("review_retry_suppressed"):
        return False
    if not state.get("review_should_retry"):
        return False
    if state.get("requires_human_review"):
        return False
    category = state.get("category")
    priority = state.get("priority")
    risk_level = state.get("risk_level")
    if category != TicketCategory.INQUIRY.value:
        return False
    if priority not in {TicketPriority.P2.value, TicketPriority.P3.value, None}:
        return False
    if risk_level not in {"low", None}:
        return False
    return True


def _build_auto_finalized_result(state: TicketState) -> str:
    """为低风险咨询生成不含占位地址的演示安全答复。"""
    content = str(state.get("content") or "").strip()
    issue_text = _join_text_list(state.get("review_issues"))

    lines = [
        "您好，知识库暂时没有收录该问题的明确答案。",
        "",
        "我无法仅凭当前知识库确认具体平台入口、报销路径或操作规则，因此不会猜测平台名称、URL 或流程。",
    ]
    if content:
        lines.extend(["", f"本次咨询：{content[:160]}"])
    if issue_text:
        lines.extend(["", f"质量审核提示：{issue_text}"])
    lines.extend([
        "",
        "建议补充：产品/服务所属平台、账号类型、订单或套餐截图、公司内部报销要求；补充后 Agent 会继续检索并处理。",
    ])
    return "\n".join(lines)


def _join_text_list(value: object) -> str:
    """把列表型审核问题合并为短文本。"""
    if not isinstance(value, list):
        return ""
    return "；".join(str(item).strip() for item in value if str(item).strip())


def _summarize_references(references: object, max_length: int = 180) -> str:
    """压缩引用文本，并移除相似度、分类等内部检索字段。"""
    if not isinstance(references, list) or not references:
        return ""
    valid_items = [
        str(item).strip()
        for item in references
        if str(item).strip() and "未检索到相关知识片段" not in str(item)
    ]
    text = " ".join(valid_items)
    if not text:
        return ""
    text = _strip_reference_metadata(" ".join(text.split()))
    return text if len(text) <= max_length else f"{text[:max_length].rstrip()}..."


def _strip_reference_metadata(text: str) -> str:
    """把检索上下文改成用户可读内容，内部检索字段只留在 Trace/开发视图。"""
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
    return re.sub(r"\s+", " ", text).strip(" ；。")


# ============================================================
# 人工审核节点
# ============================================================


def _build_trigger_reason(state: TicketState, default: str = "") -> str:
    """根据触发类型构造默认 trigger_reason（调用方未提供时使用）。"""
    existing = state.get("trigger_reason")
    if existing:
        return existing
    trigger_type = state.get("trigger_type", "")
    if trigger_type == "escalate":
        return "升级工单，需人工审核"
    if trigger_type == "review_failed":
        return f"AI 多次审核未通过（retry_count={state.get('retry_count', 0)}）"
    if trigger_type == "error_fallback":
        return state.get("error") or default or "工作流执行异常"
    if trigger_type == "user_request":
        return "用户主动申请人工审核"
    return default


async def _generate_ai_suggestion(state: TicketState) -> dict | None:
    """调用 CoordinatorAgent 生成 AI 辅助决策建议，失败时返回 None。"""
    if _coordinator_agent is None:
        return None
    try:
        return await _coordinator_agent.suggest_decision(
            state["ticket_id"],
            state.get("trigger_type", ""),
            state.get("trigger_reason") or "",
            state.get("processing_result"),
            state.get("review_score"),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"suggest_decision 失败，跳过 AI 建议: {e}")
        return None


async def human_review_wait(state: TicketState) -> dict:
    """人工审核暂停节点。

    副作用：
    1. 调用 CoordinatorAgent.suggest_decision 生成 AI 建议
    2. 写入 human_reviews 行（status=pending）
    3. 更新 tickets.status = pending_human_review（由调用方在 _run_workflow 处理）
    4. 创建 human_decision span（pending 状态）
    5. 返回结束标记（不阻塞等待，由 _run_workflow 检测后广播 review_requested）
    """
    _restore_trace_context(state)
    with log_context(agent="human_review_wait"):
        trigger_type = state.get("trigger_type", "user_request")
        trigger_reason = _build_trigger_reason(state)

        # 1. 生成 AI 建议（可选，失败不阻塞）
        ai_suggestion = await _generate_ai_suggestion(state)

        # 2. 写入 human_reviews pending 记录
        review_id = f"HR-{generate_trace_id()}"
        from datetime import datetime
        created_at = datetime.now().isoformat()
        if _db_manager is not None:
            await _db_manager.create_pending_review({
                "review_id": review_id,
                "ticket_id": state["ticket_id"],
                "trigger_type": trigger_type,
                "trigger_reason": trigger_reason,
                "ai_suggestion": ai_suggestion,
                "created_at": created_at,
            })

        # 3. 创建 pending span（span_type=human_decision，不计入 node_count）
        async with _span(
            "human_review_wait",
            span_type="human_decision",
            input_data={
                "trigger_type": trigger_type,
                "trigger_reason": trigger_reason,
                "review_id": review_id,
            },
        ) as span:
            span.set_output({
                "status": "pending",
                "review_id": review_id,
                "ai_suggestion": ai_suggestion,
            })

        # 4. 标记需广播 review_requested，更新工单状态
        return {
            "status": "pending_human_review",
            "__review_requested__": True,
            "messages": state["messages"]
            + [{
                "role": "human_review_wait",
                "content": f"工单已转入人工审核（trigger={trigger_type}, review_id={review_id}）",
            }],
        }


def _compute_ai_adopted(
    decision: str, ai_suggestion_raw: str | dict | None
) -> bool | None:
    """比较人工决策与 AI 建议，计算 ai_adopted。

    Returns:
        True 表示采纳、False 表示未采纳、None 表示无 AI 建议可比对。
    """
    if ai_suggestion_raw is None:
        return None
    if isinstance(ai_suggestion_raw, str):
        if not ai_suggestion_raw:
            return None
        import json
        try:
            ai_suggestion_raw = json.loads(ai_suggestion_raw)
        except json.JSONDecodeError:
            return None
    recommended = ai_suggestion_raw.get("recommended_decision") if isinstance(
        ai_suggestion_raw, dict
    ) else None
    if not recommended:
        return None
    return decision == recommended


async def apply_human_decision(state: TicketState) -> dict:
    """人工决策恢复节点。

    按决策矩阵路由（实际下一节点由 human_decision_router 条件边决定）：
    - approve → 沿用原 processing_result
    - rewrite → 用 rewritten_result 覆盖 processing_result
    - reprocess → 清空 processing_result + retry_count
    - reject → 标记 processing_result 为已驳回
    """
    _restore_trace_context(state)
    with log_context(agent="apply_human_decision"):
        decision_info = state.get("__human_decision__") or {}
        decision = decision_info.get("decision")
        decision_reason = decision_info.get("decision_reason")
        rewritten_result = decision_info.get("rewritten_result")
        reviewer_id = decision_info.get("reviewer_id")

        result: dict[str, Any] = {
            "messages": state["messages"]
            + [{
                "role": "apply_human_decision",
                "content": f"人工决策: {decision}（reviewer={reviewer_id}）",
            }],
            "__review_decided__": True,
        }

        result.update(
            _apply_decision_to_state(decision, state, rewritten_result)
        )

        await _persist_review_decision(
            state, decision_info, decision, decision_reason,
            rewritten_result, reviewer_id,
        )

        return result


def _apply_decision_to_state(
    decision: str | None,
    state: TicketState,
    rewritten_result: str | None,
) -> dict[str, Any]:
    """根据决策更新 state 中的 processing_result / retry_count 字段。"""
    current_result = state.get("processing_result")
    if decision == "approve":
        if _should_generate_human_approved_result(state):
            return {
                "processing_result": _build_human_approved_result(state),
                "review_score": 1.0,
                "review_should_retry": False,
            }
        return {}  # 沿用原结果
    if decision == "rewrite":
        return {
            "processing_result": rewritten_result or current_result,
            "review_score": 1.0,
            "review_should_retry": False,
        }
    if decision == "reprocess":
        return {"processing_result": None, "retry_count": 0}
    if decision == "reject":
        mark = "(已驳回) 原结果: "
        return {"processing_result": f"{mark}{current_result or '无'}"}
    return {}


def _should_generate_human_approved_result(state: TicketState) -> bool:
    """判断 approve 是否需要生成用户可读的人工处理结论。"""
    trigger_type = state.get("trigger_type")
    current_result = state.get("processing_result") or ""
    return (
        trigger_type in {"escalate", "error_fallback"}
        or current_result.startswith("已升级至人工处理")
        or "已升级至人工处理" in current_result[:80]
    )


def _build_human_approved_result(state: TicketState) -> str:
    """为人工审核通过的升级/异常工单生成最终用户答复。"""
    decision_info = state.get("__human_decision__") or {}
    decision_reason = str(decision_info.get("decision_reason") or "").strip()
    trigger_reason = str(state.get("trigger_reason") or "").strip()
    content = str(state.get("content") or "").strip()
    category = state.get("category") or "未分类"
    priority = state.get("priority") or "-"

    lines = [
        "人工审核已通过，已根据工单内容生成最终处理结论：",
        "",
        f"1. 问题判断：该工单属于 {category} 类问题，优先级 {priority}。",
    ]
    if trigger_reason:
        lines.append(f"2. 审核依据：{trigger_reason}。")
    if decision_reason:
        lines.append(f"3. 人工结论：{decision_reason}。")
    else:
        lines.append("3. 人工结论：确认该问题需要按人工审核意见处理。")
    if content:
        lines.append(f"4. 后续建议：请按上述结论继续处理；如仍异常，可补充现象截图、时间点和影响范围。")
    return "\n".join(lines)


async def _persist_review_decision(
    state: TicketState,
    decision_info: dict,
    decision: str | None,
    decision_reason: str | None,
    rewritten_result: str | None,
    reviewer_id: str | None,
) -> None:
    """更新 human_reviews 行、计算 ai_adopted、完成 span。"""
    if _db_manager is None:
        return
    from datetime import datetime
    decided_at = datetime.now().isoformat()

    pending = await _db_manager.get_pending_review_by_ticket(state["ticket_id"])
    if pending is None:
        logger.warning(
            f"apply_human_decision: 未找到 {state['ticket_id']} 的 pending 审核单"
        )
        return

    ai_adopted = _compute_ai_adopted(decision, pending.get("ai_suggestion"))

    await _db_manager.update_review_decision(
        pending["review_id"],
        {
            "decision": decision,
            "decision_reason": decision_reason,
            "rewritten_result": rewritten_result,
            "reviewer_id": reviewer_id,
            "status": "decided",
            "decided_at": decided_at,
        },
    )

    # 写入 decided span（span_type=human_decision，包含 decision/reviewer_id/ai_adopted）
    async with _span(
        "apply_human_decision",
        span_type="human_decision",
        input_data={"review_id": pending["review_id"], "decision": decision},
    ) as span:
        span.set_output({
            "status": "decided",
            "decision": decision,
            "reviewer_id": reviewer_id,
            "ai_adopted": ai_adopted,
        })
        span.set_metadata({
            "decision_reason": decision_reason,
            "rewritten_result": rewritten_result,
        })


def human_decision_router(
    state: TicketState,
) -> Literal["notify", "process", "complete"]:
    """根据人工决策结果路由到下一节点。"""
    decision_info = state.get("__human_decision__") or {}
    decision = decision_info.get("decision")
    if decision in ("approve", "rewrite"):
        return "notify"
    if decision == "reprocess":
        return "process"
    if decision == "reject":
        return "complete"
    return "notify"  # 默认走 notify（理论上不会到达）


# ============================================================
# 图构建
# ============================================================


def build_ticket_graph(
    settings: Settings | None = None,
    agents: dict | None = None,
    trace_manager: "TraceManager | None" = None,
    db_manager: "DatabaseManager | None" = None,
) -> StateGraph:
    """构建工单处理状态图。

    Args:
        settings: 系统配置（预留，当前未使用）
        agents: Agent 实例字典，支持以下键：
            - "classifier": ClassifierAgent 实例
            - "processor": ProcessorAgent 实例
            - "reviewer": ReviewerAgent 实例
            - "coordinator": CoordinatorAgent 实例（人工审核节点必需）
            为 None 时使用占位实现（向后兼容）
        trace_manager: Trace 实例（可选）
        db_manager: DatabaseManager 实例（人工审核节点持久化使用）

    Returns:
        已编译的 CompiledStateGraph，可直接调用 invoke/ainvoke
    """
    # 注入 Agent 和 TraceManager 到模块级变量
    global _classifier_agent, _processor_agent, _reviewer_agent  # noqa: PLW0603
    global _coordinator_agent, _trace_manager, _db_manager  # noqa: PLW0603

    if agents is not None:
        _classifier_agent = agents.get("classifier")
        _processor_agent = agents.get("processor")
        _reviewer_agent = agents.get("reviewer")
        _coordinator_agent = agents.get("coordinator")
    else:
        _classifier_agent = None
        _processor_agent = None
        _reviewer_agent = None
        _coordinator_agent = None

    _trace_manager = trace_manager
    _db_manager = db_manager

    graph = StateGraph(TicketState)

    # 添加所有节点
    graph.add_node("receive", receive)
    graph.add_node("classify", classify)
    graph.add_node("route", route)
    graph.add_node("process", process)
    graph.add_node("review", review)
    graph.add_node("auto_reply", auto_reply)
    graph.add_node("escalate", escalate)
    graph.add_node("notify", notify)
    graph.add_node("complete", complete)
    graph.add_node("handle_failure", handle_failure)
    graph.add_node("retry_check", retry_check)
    graph.add_node("request_user_input", request_user_input)
    graph.add_node("finalize_knowledge_gap", finalize_knowledge_gap)
    graph.add_node("human_review_wait", human_review_wait)
    graph.add_node("apply_human_decision", apply_human_decision)

    # 定义边：线性流程
    graph.add_edge(START, "receive")
    graph.add_edge("receive", "classify")
    graph.add_edge("classify", "route")

    # 条件路由：route -> process | auto_reply | escalate
    graph.add_conditional_edges("route", route_decision)

    # 处理路径：process -> review
    graph.add_edge("process", "review")

    # 审核决策：review -> notify | retry_check
    graph.add_conditional_edges("review", review_decision)

    # 重试决策：retry_check -> process | human_review_wait
    graph.add_conditional_edges("retry_check", retry_decision)

    # escalate 直接转入人工审核
    graph.add_edge("escalate", "human_review_wait")

    # 人工审核等待节点终止本次执行（审核恢复后由子图/单独入口处理）
    graph.add_edge("human_review_wait", END)

    # 知识盲区/信息不足时暂停，等待用户补充后由用户补充入口恢复
    graph.add_edge("request_user_input", END)
    graph.add_edge("finalize_knowledge_gap", "notify")

    # 人工决策恢复节点：按决策路由到 notify | process | complete
    graph.add_conditional_edges("apply_human_decision", human_decision_router)

    # 各路径汇聚到 notify -> complete -> END
    graph.add_edge("auto_reply", "notify")
    graph.add_edge("notify", "complete")
    graph.add_edge("complete", END)
    graph.add_edge("handle_failure", END)

    return graph.compile()


def _build_resume_subgraph() -> Any:
    """构造从 apply_human_decision 开始的恢复子图。

    子图节点：apply_human_decision → notify | process | complete → END
    LangGraph 0.2.x 不支持 start_node 参数，故通过独立子图实现恢复。
    """
    graph = StateGraph(TicketState)
    graph.add_node("apply_human_decision", apply_human_decision)
    graph.add_node("process", process)
    graph.add_node("review", review)
    graph.add_node("notify", notify)
    graph.add_node("complete", complete)
    graph.add_node("request_user_input", request_user_input)
    graph.add_node("finalize_knowledge_gap", finalize_knowledge_gap)
    graph.add_edge(START, "apply_human_decision")
    graph.add_conditional_edges("apply_human_decision", human_decision_router)
    graph.add_edge("process", "review")
    graph.add_conditional_edges("review", review_decision)
    graph.add_conditional_edges("retry_check", retry_decision)
    graph.add_node("retry_check", retry_check)
    graph.add_node("human_review_wait", human_review_wait)
    graph.add_edge("human_review_wait", END)
    graph.add_edge("request_user_input", END)
    graph.add_edge("finalize_knowledge_gap", "notify")
    graph.add_edge("notify", "complete")
    graph.add_edge("complete", END)
    return graph.compile()


def _build_user_input_resume_subgraph() -> Any:
    """构造用户补充信息后从 process 开始的恢复子图。"""
    graph = StateGraph(TicketState)
    graph.add_node("process", process)
    graph.add_node("review", review)
    graph.add_node("retry_check", retry_check)
    graph.add_node("request_user_input", request_user_input)
    graph.add_node("finalize_knowledge_gap", finalize_knowledge_gap)
    graph.add_node("human_review_wait", human_review_wait)
    graph.add_node("notify", notify)
    graph.add_node("complete", complete)
    graph.add_edge(START, "process")
    graph.add_edge("process", "review")
    graph.add_conditional_edges("review", review_decision)
    graph.add_conditional_edges("retry_check", retry_decision)
    graph.add_edge("request_user_input", END)
    graph.add_edge("finalize_knowledge_gap", "notify")
    graph.add_edge("human_review_wait", END)
    graph.add_edge("notify", "complete")
    graph.add_edge("complete", END)
    return graph.compile()


def _build_resume_state(
    ticket: dict[str, Any], decision_info: dict[str, Any]
) -> TicketState:
    """从 DB 工单快照构造 resume 子图的初始 state。"""
    return TicketState(
        ticket_id=ticket.get("ticket_id", ""),
        content=ticket.get("content", ""),
        category=ticket.get("category"),
        priority=ticket.get("priority"),
        processing_result=ticket.get("processing_result"),
        references=ticket.get("references") or [],
        review_score=ticket.get("review_score"),
        review_should_retry=None,
        review_retry_suppressed=None,
        review_issue_type=None,
        clarification_request=None,
        auto_finalized_review_failure=None,
        retry_count=ticket.get("retry_count", 0) or 0,
        status=ticket.get("status", "pending_human_review"),
        messages=[],
        error=ticket.get("error"),
        __trace_id__=ticket.get("trace_id"),
        __human_decision__=decision_info,
    )


async def _build_user_input_resume_state(
    ticket: dict[str, Any],
    messages: list[dict[str, Any]],
) -> TicketState:
    """从 DB 工单快照与沟通记录构造用户补充恢复 state。"""
    conversation_context = "\n".join(
        f"[{m.get('sender_type')}] {m.get('content')}"
        for m in messages[-20:]
    )
    return TicketState(
        ticket_id=ticket.get("ticket_id", ""),
        content=ticket.get("content", ""),
        category=ticket.get("category"),
        priority=ticket.get("priority"),
        processing_result=None,
        references=ticket.get("references") or [],
        review_score=None,
        review_should_retry=None,
        review_retry_suppressed=None,
        review_issue_type=None,
        clarification_request=None,
        auto_finalized_review_failure=None,
        retry_count=0,
        status="processing",
        messages=[],
        error=ticket.get("error"),
        __trace_id__=ticket.get("trace_id"),
        conversation_context=conversation_context,
    )


async def pause_for_user_input(
    app: Any,
    ticket_id: str,
    decision_reason: str,
    reviewer_id: str,
) -> dict[str, Any]:
    """人工请求用户补充信息，关闭审核单并暂停工单。"""
    global _trace_manager  # noqa: PLW0603
    db_manager = app.state.db_manager
    app_trace_manager = getattr(app.state, "trace_manager", None)
    if app_trace_manager is not None:
        _trace_manager = app_trace_manager
    pending = await db_manager.get_pending_review_by_ticket(ticket_id)
    if pending is None:
        logger.warning(f"pause_for_user_input: 未找到 {ticket_id} 的 pending 审核单")
        return {
            "ticket_id": ticket_id,
            "status": "waiting_user_input",
            "next_node": "waiting_user_input",
            "workflow_resumed": False,
        }

    from datetime import datetime
    decided_at = datetime.now().isoformat()

    await db_manager.update_review_decision(
        pending["review_id"],
        {
            "decision": "request_info",
            "decision_reason": decision_reason,
            "reviewer_id": reviewer_id,
            "status": "decided",
            "decided_at": decided_at,
        },
    )

    existing = await db_manager.get_ticket(ticket_id) or {}
    await db_manager.save_ticket({
        **existing,
        "ticket_id": ticket_id,
        "status": "waiting_user_input",
    })

    await db_manager.create_ticket_message({
        "message_id": f"TM-{generate_trace_id()}",
        "ticket_id": ticket_id,
        "sender_type": "reviewer",
        "sender_id": reviewer_id,
        "content": decision_reason,
        "metadata": {
            "source": "request_info",
            "review_id": pending["review_id"],
        },
    })
    trace = await db_manager.get_trace_by_ticket(ticket_id)
    trace_id = trace.get("trace_id") if trace else None
    if trace_id and _trace_manager is not None:
        async with _trace_manager.start_span(
            "user_input_requested",
            "node",
            trace_id=trace_id,
            input_data={
                "ticket_id": ticket_id,
                "review_id": pending["review_id"],
                "reviewer_id": reviewer_id,
                "reason_length": len(decision_reason),
            },
        ) as span:
            span.set_output({"status": "waiting_user_input"})

    return {
        "status": "ok",
        "ticket_id": ticket_id,
        "next_node": "waiting_user_input",
        "workflow_resumed": False,
    }


async def resume_from_user_input(app: Any, ticket_id: str) -> dict[str, Any]:
    """用户补充信息后，从 process 节点恢复工单处理。"""
    global _active_trace_id, _trace_manager  # noqa: PLW0603
    db_manager = app.state.db_manager
    db_tool = app.state.db_tool
    app_trace_manager = getattr(app.state, "trace_manager", None)
    if app_trace_manager is not None:
        _trace_manager = app_trace_manager
    existing = await db_manager.get_ticket(ticket_id) or {}
    messages = await db_manager.list_ticket_messages(ticket_id)
    initial_state = await _build_user_input_resume_state(existing, messages)
    trace = await db_manager.get_trace_by_ticket(ticket_id)
    trace_id = trace.get("trace_id") if trace else None
    if trace_id:
        from src.multi_agent_system.core.trace import current_trace_id

        current_trace_id.set(trace_id)
        _active_trace_id = trace_id
        initial_state["__trace_id__"] = trace_id

    await db_manager.save_ticket({
        **existing,
        "ticket_id": ticket_id,
        "processing_result": None,
        "review_score": None,
        "retry_count": 0,
        "status": "processing",
    })

    subgraph = _build_user_input_resume_subgraph()
    next_node: str | None = None
    final_status = "processing"
    current_snapshot: dict[str, Any] = {
        **existing,
        "processing_result": None,
        "review_score": None,
        "retry_count": 0,
        "status": "processing",
    }
    latest_message = messages[-1] if messages else {}
    async with _span(
        "user_input_resume",
        input_data={
            "ticket_id": ticket_id,
            "message_count": len(messages),
            "latest_sender": latest_message.get("sender_type"),
        },
    ) as span:
        async for event in subgraph.astream(initial_state):
            for node_name, node_output in event.items():
                if not isinstance(node_output, dict):
                    continue
                next_node = node_name
                current_snapshot.update(node_output)
                if "status" in node_output:
                    final_status = node_output["status"]

                merged = {**current_snapshot, "ticket_id": ticket_id}
                await db_tool.save_ticket(merged)
        span.set_output({
            "next_node": next_node,
            "ticket_status": final_status,
        })

    return {
        "status": "ok",
        "ticket_id": ticket_id,
        "next_node": next_node,
        "workflow_resumed": True,
        "ticket_status": final_status,
    }


async def resume_from_human_decision(
    app: Any,
    ticket_id: str,
    decision: str,
    decision_reason: str,
    rewritten_result: str | None,
    reviewer_id: str,
) -> dict[str, Any]:
    """从人工决策节点恢复工作流执行。

    1. 从 DB 加载工单当前快照
    2. 构造含 __human_decision__ 的初始 state
    3. 调用恢复子图（从 apply_human_decision 开始）
    4. 流式处理后返回 next_node 与 workflow_resumed 标记

    Args:
        app: FastAPI app 实例（用于读取 app.state）
        ticket_id: 工单 ID
        decision: 人工决策（approve/reject/rewrite/reprocess）
        decision_reason: 决策原因
        rewritten_result: 改写后的处理结果（decision=rewrite 时使用）
        reviewer_id: 审核员 ID

    Returns:
        dict 含 ticket_id / status / next_node / workflow_resumed
    """
    db_tool = app.state.db_tool
    existing = await db_tool.get_ticket(ticket_id) or {}

    decision_info = {
        "decision": decision,
        "decision_reason": decision_reason,
        "rewritten_result": rewritten_result,
        "reviewer_id": reviewer_id,
    }
    initial_state = _build_resume_state(existing, decision_info)

    # 恢复 trace context
    trace_id = initial_state.get("__trace_id__")
    if trace_id:
        from src.multi_agent_system.core.trace import current_trace_id
        current_trace_id.set(trace_id)

    subgraph = _build_resume_subgraph()

    next_node: str | None = None
    final_status = initial_state.get("status", "processing")
    # 累积更新：每次迭代基于上一次的状态叠加 node_output，避免后续节点
    # 未返回某字段时被 initial existing 快照回写覆盖（rewrite 后被旧值覆盖等场景）
    current_snapshot: dict[str, Any] = dict(existing)
    async for event in subgraph.astream(initial_state):
        for node_name, node_output in event.items():
            if not isinstance(node_output, dict):
                continue
            next_node = node_name
            current_snapshot.update(node_output)
            if "status" in node_output:
                final_status = node_output["status"]

            # 同步工单快照到 DB（与 _run_workflow 行为对齐）
            merged = {**current_snapshot, "ticket_id": ticket_id}
            await db_tool.save_ticket(merged)

    return {
        "ticket_id": ticket_id,
        "status": final_status,
        "next_node": next_node,
        "workflow_resumed": True,
    }
