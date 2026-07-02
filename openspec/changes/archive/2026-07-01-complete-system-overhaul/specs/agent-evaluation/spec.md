## ADDED Requirements

### Requirement: Subjective quality assessment via ReviewerAgent
The system SHALL use ReviewerAgent to evaluate processing results across accuracy, feasibility, completeness, and professionalism dimensions.

#### Scenario: Reviewer scores completed processing
- **WHEN** a ticket reaches the review node
- **THEN** ReviewerAgent SHALL analyze the processing result against the original ticket content
- **AND** output a score from 0.0 to 1.0 with detailed feedback

### Requirement: Objective metrics collected for every ticket
The system SHALL record objective metrics for each ticket processing lifecycle, including resolution status, retry count, token consumption, and processing duration.

#### Scenario: Metrics recorded on completion
- **WHEN** a ticket reaches `completed` or `failed` status
- **THEN** the system SHALL record: total_duration, token_count, tool_call_count, retry_count, and final_status
- **AND** these metrics SHALL be queryable via the `/analytics` endpoint

#### Scenario: Success rate calculation
- **WHEN** the analytics endpoint is queried for resolution stats
- **THEN** the system SHALL calculate success_rate as `completed / (completed + failed)`
- **AND** average retries per ticket

### Requirement: User satisfaction feedback collected
The system SHALL provide a mechanism for users to submit satisfaction feedback on completed tickets.

#### Scenario: User submits thumbs up/down
- **WHEN** a user submits feedback (`satisfied: true/false`) for a ticket
- **THEN** the system SHALL store the feedback in the `tickets` table
- **AND** update the user's average satisfaction score in the `users` table

#### Scenario: Feedback influences pattern learning
- **WHEN** a ticket receives `satisfied: true` and `review_score >= 0.8`
- **THEN** the system SHALL prioritize indexing this solution into the semantic memory
