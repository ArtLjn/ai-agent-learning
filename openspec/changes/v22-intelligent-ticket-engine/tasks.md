## 1. Ticket Understanding And Classification

- [ ] 1.1 Review TicketIntentAgent and ClassifierAgent outputs against `intelligent-ticket-engine` spec.
- [ ] 1.2 Ensure intent extraction returns title, category, priority, impact, contact context, confidence, and missing fields.
- [ ] 1.3 Add or align JSON schema validation and fallback defaults for invalid LLM output.
- [ ] 1.4 Add keyword and rule fallback for category, priority, and route reason.
- [ ] 1.5 Add tests for valid extraction, invalid output fallback, and route reason generation.

## 2. Knowledge Retrieval Enhancement

- [ ] 2.1 Review current RAG client and rag-service retrieve/rerank contract.
- [ ] 2.2 Ensure successful retrieval records hit count, top score, mode, and rerank metadata.
- [ ] 2.3 Ensure rag-service timeout, degraded health, or unavailable error degrades to empty references without failing the ticket.
- [ ] 2.4 Sanitize user-visible summaries while preserving internal references for service desk and operations views.
- [ ] 2.5 Add tests for RAG success, RAG unavailable fallback, and reference metadata handling.

## 3. Agent Collaboration And Quality Review

- [ ] 3.1 Align ReActProcessorAgent result generation with structured ticket and knowledge context.
- [ ] 3.2 Ensure ReviewerAgent threshold routes passed results to completion path.
- [ ] 3.3 Ensure failed review increments retry count and retries while below maximum.
- [ ] 3.4 Ensure retry limit creates service desk human review.
- [ ] 3.5 Ensure CoordinatorAgent produces human-review recommendation, reason, and confidence.

## 4. Workflow Orchestration And Trace

- [ ] 4.1 Align LangGraph routing for complaint, P0, high risk, missing fields, review failure, and employee dissatisfaction.
- [ ] 4.2 Ensure employee supplement rebuilds conversation context and resumes from processing node.
- [ ] 4.3 Write decision metadata for classify, route, process, review, retry_check, and human_review_wait spans.
- [ ] 4.4 Ensure workflow node broadcasts reflect real processing states without artificial delay.

## 5. Verification

- [ ] 5.1 Add workflow tests for normal completion, high-risk handoff, review retry, retry exhaustion, and employee supplement recovery.
- [ ] 5.2 Add trace tests for required decision metadata on route and quality decisions.
- [ ] 5.3 Run targeted pytest for agents, workflow graph, API fallback, and ticket isolation.
