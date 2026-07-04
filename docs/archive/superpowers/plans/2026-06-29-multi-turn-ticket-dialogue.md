# Multi-Turn Ticket Dialogue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real multi-turn ticket clarification loop where reviewers can request user input, users reply in the ticket detail page, and the same ticket resumes AI processing with the conversation context.

**Architecture:** Extend the existing human-review pause/resume model with a new `waiting_user_input` ticket state and a persisted `ticket_messages` timeline. `request_info` is handled as a human-review decision that closes the review, writes a reviewer message, and pauses the ticket until a user message triggers a process/review resume subgraph.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, LangGraph, Pydantic v2, React, TanStack Query, TypeScript, pytest, Vitest-free build verification.

---

## File Structure

- Modify `src/multi_agent_system/models/ticket.py`: add `WAITING_USER_INPUT`.
- Modify `src/multi_agent_system/models/review.py`: add `REQUEST_INFO` review decision.
- Create `src/multi_agent_system/models/message.py`: Pydantic DTOs for ticket messages.
- Modify `src/multi_agent_system/models/db.py`: add `TicketMessageORM`.
- Modify `src/multi_agent_system/core/database.py`: add message CRUD methods and include `TicketMessageORM` in imports.
- Modify `src/multi_agent_system/workflow/state.py`: add optional `conversation_context`.
- Modify `src/multi_agent_system/workflow/graph.py`: add user-input resume state/subgraph and request-info decision behavior.
- Modify `src/multi_agent_system/api/routes.py`: add message endpoints and route `request_info` decisions without running the human-decision subgraph.
- Modify `src/multi_agent_system/agents/processor_react.py`: include conversation context in processing input.
- Modify `web/src/types/index.ts`: add `waiting_user_input`, `request_info`, and ticket message types.
- Modify `web/src/lib/api.ts` and `web/src/lib/reviews.ts`: add message API and expanded decision types.
- Modify `web/src/components/layout/StatusBadge.tsx`: display `waiting_user_input`.
- Modify `web/src/components/reviews/DecisionPanel.tsx`: add request-info decision UI.
- Modify `web/src/pages/TicketDetail.tsx`: show communication timeline and user reply form.
- Add or modify tests under `tests/multi_agent_system/` and `tests/agents/`.

## Task 1: Data Models And Database Message Store

**Files:**
- Modify: `src/multi_agent_system/models/ticket.py`
- Modify: `src/multi_agent_system/models/review.py`
- Create: `src/multi_agent_system/models/message.py`
- Modify: `src/multi_agent_system/models/db.py`
- Modify: `src/multi_agent_system/models/__init__.py`
- Modify: `src/multi_agent_system/core/database.py`
- Test: `tests/multi_agent_system/test_ticket_messages.py`

- [ ] **Step 1: Write failing database tests**

Create `tests/multi_agent_system/test_ticket_messages.py` with tests that:

```python
import pytest

pytestmark = pytest.mark.asyncio


async def test_ticket_message_round_trip(db_manager):
    await db_manager.save_ticket({
        "ticket_id": "TK-msg-1",
        "content": "退款没有到账",
        "status": "waiting_user_input",
    })

    await db_manager.create_ticket_message({
        "message_id": "TM-1",
        "ticket_id": "TK-msg-1",
        "sender_type": "reviewer",
        "sender_id": "reviewer-001",
        "content": "请补充订单号",
        "metadata": {"source": "request_info"},
    })
    await db_manager.create_ticket_message({
        "message_id": "TM-2",
        "ticket_id": "TK-msg-1",
        "sender_type": "user",
        "sender_id": "user-001",
        "content": "订单号是 123456",
    })

    rows = await db_manager.list_ticket_messages("TK-msg-1")

    assert [row["message_id"] for row in rows] == ["TM-1", "TM-2"]
    assert rows[0]["metadata"] == {"source": "request_info"}
    assert rows[1]["content"] == "订单号是 123456"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/multi_agent_system/test_ticket_messages.py -q`

Expected: FAIL because `create_ticket_message` and `TicketMessageORM` do not exist.

- [ ] **Step 3: Add enums and DTOs**

Add `WAITING_USER_INPUT = "waiting_user_input"` to `TicketStatus`.

Add `REQUEST_INFO = "request_info"` to `ReviewDecision`.

Create `src/multi_agent_system/models/message.py`:

```python
"""工单消息相关数据模型。"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "TicketMessage",
    "TicketMessageCreate",
    "TicketMessageSender",
]


class TicketMessageSender(str, Enum):
    """工单消息发送者类型。"""

    USER = "user"
    REVIEWER = "reviewer"
    SYSTEM = "system"
    AGENT = "agent"


class TicketMessageCreate(BaseModel):
    """创建工单消息的请求。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    sender_id: str | None = None

    @model_validator(mode="after")
    def _validate_content(self) -> "TicketMessageCreate":
        self.content = self.content.strip()
        if not self.content:
            raise ValueError("MESSAGE_CONTENT_REQUIRED: 消息内容不能为空")
        return self


class TicketMessage(BaseModel):
    """工单消息返回结构。"""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    ticket_id: str
    sender_type: TicketMessageSender | str
    sender_id: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | str
```

Update `src/multi_agent_system/models/__init__.py` to export these names.

- [ ] **Step 4: Add ORM and database methods**

Add `TicketMessageORM` in `models/db.py`:

```python
class TicketMessageORM(Base):
    """工单沟通消息。"""

    __tablename__ = "ticket_messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(24), nullable=False)
    sender_id: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("idx_tm_ticket_created", "ticket_id", "created_at"),
        Index("idx_tm_sender", "sender_type"),
    )
```

Import it in `core/database.py` and add:

```python
async def create_ticket_message(self, message_data: dict[str, Any]) -> None:
    async with self._session() as session:
        metadata = message_data.get("metadata") or {}
        created_at = self._parse_datetime(message_data.get("created_at")) or datetime.now()
        session.add(TicketMessageORM(
            message_id=message_data["message_id"],
            ticket_id=message_data["ticket_id"],
            sender_type=message_data["sender_type"],
            sender_id=message_data.get("sender_id"),
            content=message_data["content"],
            metadata_json=json.dumps(metadata, ensure_ascii=False),
            created_at=created_at,
        ))
        await session.commit()


async def list_ticket_messages(
    self,
    ticket_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    async with self._session() as session:
        stmt = (
            select(TicketMessageORM)
            .where(TicketMessageORM.ticket_id == ticket_id)
            .order_by(TicketMessageORM.created_at.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = []
        for obj in result.scalars().all():
            row = self._orm_to_dict(obj)
            raw = row.pop("metadata_json", None)
            try:
                row["metadata"] = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                row["metadata"] = {}
            rows.append(row)
        return rows
```

- [ ] **Step 5: Run database tests**

Run: `pytest tests/multi_agent_system/test_ticket_messages.py -q`

Expected: PASS.

## Task 2: Request-Info Review Decision

**Files:**
- Modify: `src/multi_agent_system/models/review.py`
- Modify: `src/multi_agent_system/workflow/graph.py`
- Modify: `src/multi_agent_system/api/routes.py`
- Test: `tests/multi_agent_system/test_review_request_info.py`

- [ ] **Step 1: Write failing API/workflow test**

Create `tests/multi_agent_system/test_review_request_info.py` with:

```python
import pytest

pytestmark = pytest.mark.asyncio


async def test_request_info_decision_pauses_for_user_input(async_client, db_manager):
    await db_manager.save_ticket({
        "ticket_id": "TK-info-1",
        "content": "退款还没有到账",
        "status": "pending_human_review",
    })
    await db_manager.create_pending_review({
        "review_id": "HR-info-1",
        "ticket_id": "TK-info-1",
        "trigger_type": "review_failed",
        "trigger_reason": "缺少订单号",
        "ai_suggestion": None,
    })

    response = await async_client.post(
        "/api/reviews/TK-info-1/decision",
        json={
            "decision": "request_info",
            "decision_reason": "请补充订单号",
            "reviewer_id": "reviewer-001",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["next_node"] == "waiting_user_input"
    assert body["workflow_resumed"] is False

    ticket = await db_manager.get_ticket("TK-info-1")
    assert ticket["status"] == "waiting_user_input"

    review = await db_manager.list_reviews_by_ticket("TK-info-1")
    assert review[-1]["decision"] == "request_info"
    assert review[-1]["status"] == "decided"

    messages = await db_manager.list_ticket_messages("TK-info-1")
    assert messages[-1]["sender_type"] == "reviewer"
    assert messages[-1]["content"] == "请补充订单号"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/multi_agent_system/test_review_request_info.py -q`

Expected: FAIL because `request_info` is not a valid decision or route.

- [ ] **Step 3: Allow request-info validation**

Update `ReviewDecisionRequest._validate_decision_fields` so `request_info` requires `decision_reason` but does not require `rewritten_result`.

- [ ] **Step 4: Handle request-info in route before resume**

In `submit_review_decision`, branch before `resume_from_human_decision`:

```python
if req.decision.value == "request_info":
    result = await pause_for_user_input(
        app=app,
        ticket_id=ticket_id,
        decision_reason=req.decision_reason,
        reviewer_id=req.reviewer_id,
    )
    await _broadcast_review_event("user_input_requested", ticket_id, {
        "reviewer_id": req.reviewer_id,
        "message": req.decision_reason,
    })
    await _broadcast_ticket_update(
        ticket_id=ticket_id,
        status="waiting_user_input",
        message=req.decision_reason,
        node="request_info",
        data={"reviewer_id": req.reviewer_id},
    )
    return result
```

Add `pause_for_user_input` in `workflow/graph.py`. It should:

1. Load current pending review.
2. Update review decision to `request_info`, status `decided`.
3. Save ticket status `waiting_user_input`.
4. Create reviewer message with `message_id=f"TM-{generate_trace_id()}"`.
5. Return `{"status": "ok", "ticket_id": ticket_id, "next_node": "waiting_user_input", "workflow_resumed": False}`.

- [ ] **Step 5: Run request-info test**

Run: `pytest tests/multi_agent_system/test_review_request_info.py -q`

Expected: PASS.

## Task 3: User Message API And Resume From User Input

**Files:**
- Modify: `src/multi_agent_system/workflow/state.py`
- Modify: `src/multi_agent_system/workflow/graph.py`
- Modify: `src/multi_agent_system/api/routes.py`
- Modify: `src/multi_agent_system/agents/processor_react.py`
- Test: `tests/multi_agent_system/test_user_input_resume.py`
- Test: `tests/agents/test_processor_react.py`

- [ ] **Step 1: Write failing resume API test**

Create `tests/multi_agent_system/test_user_input_resume.py` with:

```python
import pytest

pytestmark = pytest.mark.asyncio


async def test_user_message_requires_waiting_state(async_client, db_manager):
    await db_manager.save_ticket({
        "ticket_id": "TK-msg-state",
        "content": "退款问题",
        "status": "completed",
    })

    response = await async_client.post(
        "/api/tickets/TK-msg-state/messages",
        json={"content": "订单号是 123456", "sender_id": "user-001"},
    )

    assert response.status_code == 409


async def test_user_message_resumes_workflow(async_client, db_manager, monkeypatch):
    calls = []

    async def fake_resume(app, ticket_id):
        calls.append(ticket_id)
        return {
            "status": "ok",
            "ticket_id": ticket_id,
            "workflow_resumed": True,
            "next_node": "process",
        }

    monkeypatch.setattr(
        "src.multi_agent_system.api.routes.resume_from_user_input",
        fake_resume,
        raising=False,
    )

    await db_manager.save_ticket({
        "ticket_id": "TK-user-1",
        "content": "退款没有到账",
        "status": "waiting_user_input",
    })

    response = await async_client.post(
        "/api/tickets/TK-user-1/messages",
        json={"content": "订单号是 123456", "sender_id": "user-001"},
    )

    assert response.status_code == 200
    assert response.json()["workflow_resumed"] is True
    assert calls == ["TK-user-1"]

    messages = await db_manager.list_ticket_messages("TK-user-1")
    assert messages[-1]["sender_type"] == "user"
    assert messages[-1]["content"] == "订单号是 123456"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/multi_agent_system/test_user_input_resume.py -q`

Expected: FAIL because message endpoints do not exist.

- [ ] **Step 3: Add routes**

In `routes.py`, import `TicketMessageCreate`. Add:

```python
@router.get("/tickets/{ticket_id}/messages", response_model=list[dict])
async def list_ticket_messages(ticket_id: str, request: Request) -> list[dict]:
    ticket = await request.app.state.db_tool.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail=f"工单 {ticket_id} 不存在")
    return await request.app.state.db_manager.list_ticket_messages(ticket_id)


@router.post("/tickets/{ticket_id}/messages", response_model=dict)
async def create_user_ticket_message(
    ticket_id: str,
    body: TicketMessageCreate,
    request: Request,
) -> dict:
    ticket = await request.app.state.db_tool.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail=f"工单 {ticket_id} 不存在")
    if ticket.get("status") != "waiting_user_input":
        raise HTTPException(
            status_code=409,
            detail=f"工单 {ticket_id} 当前不等待用户补充（当前状态: {ticket.get('status')}）",
        )
    await request.app.state.db_manager.create_ticket_message({
        "message_id": f"TM-{generate_trace_id()}",
        "ticket_id": ticket_id,
        "sender_type": "user",
        "sender_id": body.sender_id,
        "content": body.content,
        "metadata": {"source": "user_input"},
    })
    await _broadcast_ticket_update(
        ticket_id=ticket_id,
        status="waiting_user_input",
        message="用户已补充信息",
        node="ticket_message_created",
    )
    from src.multi_agent_system.workflow.graph import resume_from_user_input
    result = await resume_from_user_input(request.app, ticket_id)
    await _broadcast_ticket_update(
        ticket_id=ticket_id,
        status="processing",
        message="已收到补充信息，继续处理",
        node="workflow_resumed_from_user_input",
    )
    return result
```

- [ ] **Step 4: Add user-input resume subgraph**

In `workflow/state.py`, add:

```python
conversation_context: str | None
```

In `workflow/graph.py`, add `_build_user_input_resume_subgraph`, `_build_user_input_resume_state`, and `resume_from_user_input`. The subgraph starts at `process`, then `review`, then existing `review_decision`.

`_build_user_input_resume_state` should load messages with `db_manager.list_ticket_messages(ticket_id)`, format:

```python
conversation_context = "\n".join(
    f"[{m.get('sender_type')}] {m.get('content')}"
    for m in messages[-20:]
)
```

It should reset `retry_count=0`, `processing_result=None`, `review_score=None`, `status="processing"`.

- [ ] **Step 5: Include conversation context in processor**

In `processor_react.py`, find the processing entrypoint and append `conversation_context` when present:

```python
if conversation_context:
    content = (
        f"{content}\n\n补充信息记录：\n{conversation_context}\n"
        "请结合原始工单和补充信息处理。"
    )
```

Add or update an agent test so a fake processor receives both “退款没有到账” and “订单号是 123456”.

- [ ] **Step 6: Run backend tests**

Run:

```bash
pytest tests/multi_agent_system/test_ticket_messages.py \
  tests/multi_agent_system/test_review_request_info.py \
  tests/multi_agent_system/test_user_input_resume.py \
  tests/agents/test_processor_react.py -q
```

Expected: PASS.

## Task 4: Frontend Types, APIs, And Status Display

**Files:**
- Modify: `web/src/types/index.ts`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/lib/reviews.ts`
- Modify: `web/src/components/layout/StatusBadge.tsx`
- Modify: `web/src/pages/Dashboard.tsx`
- Modify: `web/src/pages/Tickets.tsx`

- [ ] **Step 1: Update TypeScript types**

Add `waiting_user_input` to `TicketStatus`.

Add `request_info` to `ReviewDecision`.

Add:

```ts
export interface TicketMessage {
  message_id: string
  ticket_id: string
  sender_type: 'user' | 'reviewer' | 'system' | 'agent' | string
  sender_id: string | null
  content: string
  metadata: Record<string, unknown>
  created_at: string
}

export interface TicketMessageCreateRequest {
  content: string
  sender_id?: string
}
```

- [ ] **Step 2: Add API client methods**

In `api.ts`, add:

```ts
getTicketMessages: (id: string) =>
  request<TicketMessage[]>(`/tickets/${encodeURIComponent(id)}/messages`),
createTicketMessage: (id: string, data: TicketMessageCreateRequest) =>
  request<ApiRecord>(`/tickets/${encodeURIComponent(id)}/messages`, {
    method: 'POST',
    body: JSON.stringify(data),
  }),
```

Update imports for new types.

- [ ] **Step 3: Display status**

In `StatusBadge.tsx`, add:

```ts
waiting_user_input: 'bg-warning/15 text-warning'
```

and label:

```ts
waiting_user_input: '待用户补充'
```

In `Dashboard.tsx`, add to status label map and `activeStatuses`.

In `Tickets.tsx`, add a filter option for `waiting_user_input`.

- [ ] **Step 4: Build frontend**

Run: `npm run build`

Expected: TypeScript and Vite build pass.

## Task 5: Frontend Review Decision UI

**Files:**
- Modify: `web/src/components/reviews/DecisionPanel.tsx`
- Modify: `web/src/components/reviews/reviewUtils.ts`
- Modify: `web/src/pages/ReviewWorkbench.tsx`

- [ ] **Step 1: Add review metadata**

In `reviewUtils.ts`, add decision label/color for `request_info`.

- [ ] **Step 2: Add button**

In `DecisionPanel.tsx`, import `HelpCircle` or `MessageSquare` from lucide-react. Add option:

```ts
{ key: 'request_info', label: '请求补充', icon: MessageSquare, color: 'bg-info text-info-foreground hover:bg-info/90' }
```

If local design tokens do not have `info`, use `bg-primary text-primary-foreground hover:bg-primary/90`.

- [ ] **Step 3: Adjust field labels**

When `pendingDecision === 'request_info'`, show reason label “补充说明（必填）” and placeholder “请输入希望用户补充的信息，例如订单号、支付流水号...”.

- [ ] **Step 4: Update submit response handling**

In `ReviewWorkbench.tsx`, allow `next_node === "waiting_user_input"` and keep existing queue refetch behavior. Toast should say “已请求用户补充信息”.

- [ ] **Step 5: Build frontend**

Run: `npm run build`

Expected: PASS.

## Task 6: Frontend Ticket Detail Message Timeline

**Files:**
- Modify: `web/src/pages/TicketDetail.tsx`

- [ ] **Step 1: Add query and mutation**

Use TanStack Query directly in `TicketDetail.tsx`:

```ts
const { data: ticketMessages = [] } = useQuery({
  queryKey: ['ticketMessages', id],
  queryFn: () => api.getTicketMessages(id!),
  enabled: !!id,
})

const submitMessage = useMutation({
  mutationFn: (content: string) => api.createTicketMessage(id!, {
    content,
    sender_id: 'user-001',
  }),
  onSuccess: () => {
    setReply('')
    qc.invalidateQueries({ queryKey: ['ticketMessages', id] })
    qc.invalidateQueries({ queryKey: ['ticket', id] })
    qc.invalidateQueries({ queryKey: ['tickets'] })
  },
})
```

- [ ] **Step 2: Add communication card**

Add a card under “工单信息” named “沟通记录”. It shows `ticketMessages` in time order with compact sender badges. Use existing `Card`, `Textarea`, `Button`, `ScrollArea`.

- [ ] **Step 3: Add reply form only while waiting**

If `ticket.status === 'waiting_user_input'`, show a textarea and submit button. Disable submit when blank or mutation is pending.

- [ ] **Step 4: WebSocket invalidation**

In `refreshTicketSnapshot`, also invalidate:

```ts
qc.invalidateQueries({ queryKey: ['ticketMessages', id] })
```

- [ ] **Step 5: Build frontend**

Run: `npm run build`

Expected: PASS.

## Task 7: End-To-End Verification And Deployment

**Files:**
- Modify only if failures reveal scoped issues.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
pytest tests/multi_agent_system/test_ticket_messages.py \
  tests/multi_agent_system/test_review_request_info.py \
  tests/multi_agent_system/test_user_input_resume.py \
  tests/multi_agent_system tests/agents -q
```

Expected: all tests pass.

- [ ] **Step 2: Run frontend build**

Run: `npm run build`

Expected: PASS.

- [ ] **Step 3: Run local service smoke test**

Start service using existing project command or deployment script local mode if available. Then verify:

```bash
curl -s http://127.0.0.1:9001/health
```

Expected: JSON health response.

- [ ] **Step 4: Manual API smoke**

Use local API to:

1. Create a ticket.
2. Force or seed pending human review in test database if needed.
3. Submit `request_info`.
4. POST user message.
5. Confirm ticket leaves `waiting_user_input`.

- [ ] **Step 5: Deploy**

Run:

```bash
bash deploy/deploy.sh
```

Expected: frontend build succeeds, backend syncs, service restarts, and public health check passes.

- [ ] **Step 6: Clean remote test artifacts if deploy syncs cache**

Run:

```bash
ssh root@43.155.217.74 'rm -rf /root/workspace/ai-agent-learning/.pytest_cache && systemctl is-active ai-agent-learning'
```

Expected: `active`.

