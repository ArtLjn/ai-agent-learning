## 1. Service Desk Queue And Review

- [x] 1.1 Review current review workbench APIs, `human_reviews` schema, and frontend queue behavior.
- [x] 1.2 Add or align filters for status, category, priority, and trigger type in pending work queue.
- [x] 1.3 Ensure work item detail includes original request, classification, priority, processing result, messages, and trace summary.
- [x] 1.4 Align review decision API with approve, rewrite, reprocess, reject, and request_info.
- [x] 1.5 Ensure request_info creates employee-visible reviewer message and sets ticket to `waiting_user_input`.

## 2. Knowledge Maintenance

- [x] 2.1 Review existing knowledge page and rag-service ingestion contract.
- [x] 2.2 Add knowledge document status for pending, ingesting, published, failed, and rolled_back where missing.
- [x] 2.3 Add or align upload, ingest result display, update, delete, and version rollback flows.
- [x] 2.4 Ensure rollback creates a new active version and preserves version history.

## 3. Operations Analytics For Service Desk

- [x] 3.1 Add or align status summary metrics for received, processing, waiting_user_input, pending_human_review, completed, and failed.
- [x] 3.2 Add AI recommendation adoption rate from coordinator recommendation and reviewer decision.
- [x] 3.3 Add employee feedback summary and dissatisfied-ticket drilldown.
- [x] 3.4 Update dashboard or service desk analytics view to show review quality and feedback data.

## 4. Verification

- [x] 4.1 Add backend tests for all five review decisions and state transitions.
- [x] 4.2 Add backend tests for knowledge upload, publish failure, and version rollback.
- [x] 4.3 Add aggregation tests for review adoption and employee feedback.
- [x] 4.4 Run targeted pytest and frontend lint/build checks.
