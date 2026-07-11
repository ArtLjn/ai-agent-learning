## 1. Service Desk Queue And Review

- [ ] 1.1 Review current review workbench APIs, `human_reviews` schema, and frontend queue behavior.
- [ ] 1.2 Add or align filters for status, category, priority, and trigger type in pending work queue.
- [ ] 1.3 Ensure work item detail includes original request, classification, priority, processing result, messages, and trace summary.
- [ ] 1.4 Align review decision API with approve, rewrite, reprocess, reject, and request_info.
- [ ] 1.5 Ensure request_info creates employee-visible reviewer message and sets ticket to `waiting_user_input`.

## 2. Knowledge Maintenance

- [ ] 2.1 Review existing knowledge page and rag-service ingestion contract.
- [ ] 2.2 Add knowledge document status for pending, ingesting, published, failed, and rolled_back where missing.
- [ ] 2.3 Add or align upload, ingest result display, update, delete, and version rollback flows.
- [ ] 2.4 Ensure rollback creates a new active version and preserves version history.

## 3. Operations Analytics For Service Desk

- [ ] 3.1 Add or align status summary metrics for received, processing, waiting_user_input, pending_human_review, completed, and failed.
- [ ] 3.2 Add AI recommendation adoption rate from coordinator recommendation and reviewer decision.
- [ ] 3.3 Add employee feedback summary and dissatisfied-ticket drilldown.
- [ ] 3.4 Update dashboard or service desk analytics view to show review quality and feedback data.

## 4. Verification

- [ ] 4.1 Add backend tests for all five review decisions and state transitions.
- [ ] 4.2 Add backend tests for knowledge upload, publish failure, and version rollback.
- [ ] 4.3 Add aggregation tests for review adoption and employee feedback.
- [ ] 4.4 Run targeted pytest and frontend lint/build checks.
