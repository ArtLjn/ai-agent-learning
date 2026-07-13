## 1. Ticket Understanding And Classification

- [x] 1.1 Review TicketIntentAgent and ClassifierAgent outputs against `intelligent-ticket-engine` spec.
- [x] 1.2 Ensure intent extraction returns title, category, priority, impact, contact context, confidence, and missing fields.
- [x] 1.3 Add or align JSON schema validation and fallback defaults for invalid LLM output.
- [x] 1.4 Add keyword and rule fallback for category, priority, and route reason.
- [x] 1.5 Add tests for valid extraction, invalid output fallback, and route reason generation.

## 2. Knowledge Retrieval Enhancement

- [x] 2.1 Review current RAG client and rag-service retrieve/rerank contract.
- [x] 2.2 Ensure successful retrieval records hit count, top score, mode, and rerank metadata.
- [x] 2.3 Ensure rag-service timeout, degraded health, or unavailable error degrades to empty references without failing the ticket.
- [x] 2.4 Sanitize user-visible summaries while preserving internal references for service desk and operations views.
- [x] 2.5 Add tests for RAG success, RAG unavailable fallback, and reference metadata handling.

## 3. Agent Collaboration And Quality Review

- [x] 3.1 Align ReActProcessorAgent result generation with structured ticket and knowledge context.
- [x] 3.2 Ensure ReviewerAgent threshold routes passed results to completion path.
- [x] 3.3 Ensure failed review increments retry count and retries while below maximum.
- [x] 3.4 Ensure retry limit creates service desk human review.
- [x] 3.5 Ensure CoordinatorAgent produces human-review recommendation, reason, and confidence.

## 4. Workflow Orchestration And Trace

- [x] 4.1 Align LangGraph routing for complaint, P0, high risk, missing fields, review failure, and employee dissatisfaction.
- [x] 4.2 Ensure employee supplement rebuilds conversation context and resumes from processing node.
- [x] 4.3 Write decision metadata for classify, route, process, review, retry_check, and human_review_wait spans.
- [x] 4.4 Ensure workflow node broadcasts reflect real processing states without artificial delay.

## 5. Verification

- [x] 5.1 Add workflow tests for normal completion, high-risk handoff, review retry, retry exhaustion, and employee supplement recovery.
- [x] 5.2 Add trace tests for required decision metadata on route and quality decisions.
- [x] 5.3 Run targeted pytest for agents, workflow graph, API fallback, and ticket isolation.
