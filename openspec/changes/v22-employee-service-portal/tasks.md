## 1. Employee Profile And Ticket Submission

- [x] 1.1 Review current auth, user profile, ticket creation, and ticket detail APIs against `employee-service-portal` spec.
- [x] 1.2 Add or align employee profile fields for contact, department, position, and preferred categories.
- [x] 1.3 Update employee profile API and page to read and write allowed employee-facing fields.
- [x] 1.4 Update ticket creation payload to include service type and key materials metadata.
- [x] 1.5 Add frontend service type selection and key material prompts to the ticket submission flow.

## 2. Employee Progress And Supplement Flow

- [x] 2.1 Ensure ticket list and detail APIs enforce owner-only access for employee role.
- [x] 2.2 Ensure employee ticket detail returns sanitized content and hides internal retrieval or Agent metadata.
- [x] 2.3 Update ticket detail progress component to show business-friendly status and timeline.
- [x] 2.4 Restrict supplement composer to employee-owned tickets in `waiting_user_input`.
- [x] 2.5 Resume workflow after employee supplement and include recent `ticket_messages` in processing context.

## 3. Result Acceptance And Appeal

- [x] 3.1 Add or align completed-ticket feedback API for satisfied and dissatisfied outcomes.
- [x] 3.2 Prevent duplicate final feedback for the same ticket.
- [x] 3.3 Create `user_request` human review when employee submits dissatisfied feedback.
- [x] 3.4 Update ticket detail UI to show result acceptance, appeal reason, and history.

## 4. Verification

- [x] 4.1 Add backend tests for employee profile, owner-only ticket access, supplement conflict, and feedback appeal.
- [x] 4.2 Add frontend utility or component tests for employee progress, supplement permission, and sanitized presentation.
- [x] 4.3 Run targeted pytest and frontend lint/build checks.
- [x] 4.4 Update docs or screenshots if the employee service flow changes visibly. Existing v2.2 design docs already cover the employee service flow; no screenshot artifact is maintained.
