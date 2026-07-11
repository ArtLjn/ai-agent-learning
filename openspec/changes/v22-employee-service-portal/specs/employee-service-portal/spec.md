## ADDED Requirements

### Requirement: Employee profile is available for service requests
The system SHALL allow an authenticated internal employee to view and maintain service profile fields used by ticket handling.

#### Scenario: Employee updates profile
- **WHEN** an authenticated employee updates nickname, contact, department, position, or preferred categories
- **THEN** the system MUST persist the allowed fields and return the updated profile without exposing secrets or password hashes

#### Scenario: Unauthenticated profile access
- **WHEN** an unauthenticated request accesses the employee profile endpoint
- **THEN** the system MUST reject the request with an authentication error

### Requirement: Employee can submit a structured service ticket
The system SHALL allow an employee to submit a service request with service type, natural language description, and optional key materials.

#### Scenario: Successful service ticket submission
- **WHEN** an authenticated employee submits service type, problem description, and optional key materials
- **THEN** the system MUST create a ticket owned by that employee and start the intelligent processing workflow

#### Scenario: Empty problem description
- **WHEN** an employee submits a ticket without problem description
- **THEN** the system MUST reject the request and MUST NOT create a ticket

### Requirement: Employee can track ticket progress with a sanitized view
The system SHALL show the employee only their own tickets, progress state, user-visible content, and user-visible processing result.

#### Scenario: Employee views own ticket
- **WHEN** an employee requests a ticket they own
- **THEN** the system MUST return the original employee-facing description, status, progress information, messages, and sanitized processing result

#### Scenario: Employee attempts cross-ticket access
- **WHEN** an employee requests a ticket owned by another employee
- **THEN** the system MUST reject the request with an authorization error

#### Scenario: Internal metadata is hidden
- **WHEN** a ticket contains knowledge similarity, confidence, Agent judgment, or raw retrieval metadata
- **THEN** the employee-facing response MUST NOT expose those internal fields

### Requirement: Employee can supplement information when requested
The system SHALL allow an employee to add supplementary information only when the ticket is waiting for employee input.

#### Scenario: Supplement accepted during waiting state
- **WHEN** a ticket is in `waiting_user_input` and the owner submits supplementary information
- **THEN** the system MUST store the message and resume processing with the conversation context

#### Scenario: Supplement rejected outside waiting state
- **WHEN** a ticket is not in `waiting_user_input` and the owner submits supplementary information
- **THEN** the system MUST reject the request with a conflict response

### Requirement: Employee can accept or appeal a completed result
The system SHALL allow the employee to provide one final feedback decision for a completed ticket.

#### Scenario: Employee accepts result
- **WHEN** a completed ticket owner submits satisfied feedback
- **THEN** the system MUST record the feedback and include it in satisfaction statistics

#### Scenario: Employee appeals result
- **WHEN** a completed ticket owner submits dissatisfied feedback with a reason
- **THEN** the system MUST create a service desk human review request with trigger type `user_request`

#### Scenario: Duplicate final feedback
- **WHEN** an employee submits final feedback for a ticket that already has final feedback
- **THEN** the system MUST reject the duplicate request
