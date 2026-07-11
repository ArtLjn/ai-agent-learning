## ADDED Requirements

### Requirement: Service desk can review pending work queue
The system SHALL provide a service desk queue for tickets that require human attention.

#### Scenario: Filter pending human reviews
- **WHEN** a service desk user filters by status, category, priority, or trigger type
- **THEN** the system MUST return matching pending work items sorted by latest update time

#### Scenario: View work item detail
- **WHEN** a service desk user opens a work item
- **THEN** the system MUST show the original employee request, classification, priority, processing result, messages, and trace summary

### Requirement: Service desk can submit human review decisions
The system SHALL allow service desk reviewers to submit one of the supported human review decisions.

#### Scenario: Approve or rewrite result
- **WHEN** a reviewer submits `approve` or `rewrite` for a pending review
- **THEN** the system MUST close the review and route the ticket toward employee-visible completion

#### Scenario: Reprocess result
- **WHEN** a reviewer submits `reprocess`
- **THEN** the system MUST close the review and resume intelligent processing from the processing node

#### Scenario: Request employee information
- **WHEN** a reviewer submits `request_info` with a reason
- **THEN** the system MUST close the review, set the ticket to `waiting_user_input`, and create a reviewer message visible to the employee

#### Scenario: Reject result
- **WHEN** a reviewer submits `reject`
- **THEN** the system MUST close the review and mark the ticket with the rejection outcome

### Requirement: Knowledge maintainer can manage service knowledge
The system SHALL allow service desk knowledge maintainers to upload, publish, update, and roll back service knowledge documents.

#### Scenario: Upload knowledge document
- **WHEN** a knowledge maintainer uploads a document with title, category, and content or file
- **THEN** the system MUST create a knowledge document record and call rag-service ingestion

#### Scenario: Publish ingestion result
- **WHEN** rag-service returns successful ingestion metadata
- **THEN** the system MUST show chunk count, collection, version, and published status

#### Scenario: Roll back knowledge version
- **WHEN** a knowledge maintainer selects a previous version for rollback
- **THEN** the system MUST create a new active version based on that content and preserve version history

### Requirement: Service desk can view operation analytics
The system SHALL provide analytics for ticket processing state, review quality, employee feedback, and AI adoption.

#### Scenario: View review adoption
- **WHEN** a service desk user opens the analytics view
- **THEN** the system MUST show AI recommendation adoption rate calculated from coordinator recommendation and reviewer decision

#### Scenario: View employee feedback summary
- **WHEN** tickets have satisfied or dissatisfied feedback
- **THEN** the system MUST aggregate feedback counts and link dissatisfied tickets to their review trigger
