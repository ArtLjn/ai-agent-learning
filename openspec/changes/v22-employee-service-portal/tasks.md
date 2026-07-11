## 1. Employee Profile And Ticket Submission

- [ ] 1.1 Review current auth, user profile, ticket creation, and ticket detail APIs against `employee-service-portal` spec.
- [ ] 1.2 Add or align employee profile fields for contact, department, position, and preferred categories.
- [ ] 1.3 Update employee profile API and page to read and write allowed employee-facing fields.
- [ ] 1.4 Update ticket creation payload to include service type and key materials metadata.
- [ ] 1.5 Add frontend service type selection and key material prompts to the ticket submission flow.

## 2. Employee Progress And Supplement Flow

- [ ] 2.1 Ensure ticket list and detail APIs enforce owner-only access for employee role.
- [ ] 2.2 Ensure employee ticket detail returns sanitized content and hides internal retrieval or Agent metadata.
- [ ] 2.3 Update ticket detail progress component to show business-friendly status and timeline.
- [ ] 2.4 Restrict supplement composer to employee-owned tickets in `waiting_user_input`.
- [ ] 2.5 Resume workflow after employee supplement and include recent `ticket_messages` in processing context.

## 3. Result Acceptance And Appeal

- [ ] 3.1 Add or align completed-ticket feedback API for satisfied and dissatisfied outcomes.
- [ ] 3.2 Prevent duplicate final feedback for the same ticket.
- [ ] 3.3 Create `user_request` human review when employee submits dissatisfied feedback.
- [ ] 3.4 Update ticket detail UI to show result acceptance, appeal reason, and history.

## 4. Verification

- [ ] 4.1 Add backend tests for employee profile, owner-only ticket access, supplement conflict, and feedback appeal.
- [ ] 4.2 Add frontend utility or component tests for employee progress, supplement permission, and sanitized presentation.
- [ ] 4.3 Run targeted pytest and frontend lint/build checks.
- [ ] 4.4 Update docs or screenshots if the employee service flow changes visibly.
