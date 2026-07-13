## 1. Account Governance

- [x] 1.1 Review current admin user management APIs and role model.
- [x] 1.2 Align roles for employee, service desk, and operations users.
- [x] 1.3 Implement role or status update auditing.
- [x] 1.4 Ensure disabled accounts cannot access protected business APIs.
- [x] 1.5 Update account management UI with role, status, search, and action controls.

## 2. Workflow Monitoring

- [x] 2.1 Review current Trace, Span, and state machine canvas implementation.
- [x] 2.2 Ensure state machine canvas shows nodes, edges, retry paths, human handoff, and completion paths.
- [x] 2.3 Ensure ticket trace detail shows hierarchy, node input, output, decision metadata, duration, and errors.
- [x] 2.4 Add empty-state handling when decision metadata is missing.

## 3. Strategy Debugging

- [x] 3.1 Add or align Prompt version list, create, activate, and rollback APIs.
- [x] 3.2 Add operation log entry for Prompt activation and rollback.
- [x] 3.3 Add or align RAG debug endpoint to call rag-service and expose errors.
- [x] 3.4 Add Agent statistics aggregation by Agent, time range, success rate, and error rate.
- [x] 3.5 Update system operations navigation for Prompt, RAG, Agent stats, and Trace views.

## 4. System Health And Cost

- [x] 4.1 Add or align health checks for rag-service, Qdrant, LLM, and Embedding.
- [x] 4.2 Add Token usage aggregation by date, model, and call type.
- [x] 4.3 Remove or hide per-user billing and quota controls from the operations UI.
- [x] 4.4 Add health and Token UI tests or smoke checks.

## 5. Verification

- [x] 5.1 Add backend tests for account disable, role update, and audit logging.
- [x] 5.2 Add backend tests for Trace details, Prompt activation, RAG debug, Agent stats, health, and Token aggregation.
- [x] 5.3 Run targeted pytest and frontend lint/build checks.
