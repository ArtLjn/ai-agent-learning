"""D-01 Trace 决策树 Admin API。

挂在 /api/admin 前缀，整组 require_role("admin", "developer")。

数据源：现有 traces + spans 表（C2 已落 metadata.decision 五元组）。
本路由只读：
- GET /admin/traces                     列表（按 ticket_id / status 筛选 + 分页）
- GET /admin/traces/{ticket_id}         完整 trace + span 树 + decisions 抽取
- GET /admin/traces/{ticket_id}/spans/{span_id}   span 详情（含 metadata.decision）
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from src.multi_agent_system.core.permissions import require_role

__all__ = ["router"]

router = APIRouter(
    prefix="/admin/traces",
    tags=["admin-trace"],
    dependencies=[Depends(require_role("admin", "developer"))],
)


def _parse_json_field(value: Any) -> Any:
    """span/trace 的 input/output/metadata 字段在 ORM 中存为 Text(JSON 字符串)。
    反序列化为 dict；非 JSON 字符串保留原值。
    """
    if value is None or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _build_span_tree(spans: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """扁平 span → 嵌套树。返回 (roots, all_spans_with_children)。

    - 反序列化 input/output/metadata JSON 字段
    - 给每个 span 加 children: []
    - 按 parent_span_id 串起来
    - 同步抽取含 metadata.decision 的 span 作为 decisions 列表
    """
    span_map: dict[str, dict[str, Any]] = {}
    decisions: list[dict[str, Any]] = []

    for span in spans:
        for field in ("input_data", "output_data", "metadata"):
            if field in span:
                span[field] = _parse_json_field(span.get(field))
        span["children"] = []
        span_map[span["span_id"]] = span

    roots: list[dict[str, Any]] = []
    for span in spans:
        parent_id = span.get("parent_span_id")
        if parent_id and parent_id in span_map:
            span_map[parent_id]["children"].append(span)
        else:
            roots.append(span)

        metadata = span.get("metadata")
        if isinstance(metadata, dict):
            decision = metadata.get("decision")
            if isinstance(decision, dict):
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
                    "execution": decision.get("execution"),
                    "start_time": span.get("start_time"),
                    "duration": span.get("duration"),
                })

    decisions.sort(key=lambda d: d.get("start_time") or 0)
    return roots, decisions


@router.get("")
async def list_traces(
    request: Request,
    ticket_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """trace 列表 + 分页 + ticket_id/status 筛选。"""
    db_manager = request.app.state.db_manager
    limit = page_size
    offset = (page - 1) * page_size
    traces = await db_manager.list_traces(
        status=status, ticket_id=ticket_id, limit=limit, offset=offset
    )
    total = await db_manager.count_traces(status=status, ticket_id=ticket_id)
    return {
        "items": traces,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{ticket_id}")
async def get_trace_with_spans(ticket_id: str, request: Request) -> dict[str, Any]:
    """获取工单完整 trace + span 树 + decisions 抽取。"""
    db_manager = request.app.state.db_manager
    trace = await db_manager.get_trace_by_ticket(ticket_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")

    spans = await db_manager.get_spans_by_trace(trace["trace_id"])
    span_tree, decisions = _build_span_tree(spans)
    decision_empty_state = None
    if not decisions:
        decision_empty_state = {
            "reason": "missing_decision_metadata",
            "message": "当前 trace 未记录 metadata.decision，请检查节点决策埋点。",
            "span_count": len(spans),
        }

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
        "spans": span_tree,
        "decision_count": len(decisions),
        "decisions": decisions,
        "decision_empty_state": decision_empty_state,
    }


@router.get("/{ticket_id}/spans/{span_id}")
async def get_span_detail(
    ticket_id: str, span_id: str, request: Request
) -> dict[str, Any]:
    """获取 span 详情（含 metadata.decision 五元组 + token_usage + rag_stats）。"""
    db_manager = request.app.state.db_manager
    trace = await db_manager.get_trace_by_ticket(ticket_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")

    spans = await db_manager.get_spans_by_trace(trace["trace_id"])
    target: dict[str, Any] | None = None
    for span in spans:
        if span.get("span_id") == span_id:
            target = span
            break

    if target is None:
        raise HTTPException(status_code=404, detail="Span not found")

    for field in ("input_data", "output_data", "metadata"):
        if field in target:
            target[field] = _parse_json_field(target.get(field))
    target.pop("children", None)
    return target
