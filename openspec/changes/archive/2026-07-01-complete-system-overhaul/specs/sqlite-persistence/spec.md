## ADDED Requirements

### Requirement: SQLite database replaces in-memory storage
The system SHALL use SQLite as the primary persistence layer for tickets, users, checkpoints, and patterns, replacing the current in-memory dict storage.

#### Scenario: Database initialization on startup
- **WHEN** the FastAPI application starts
- **THEN** the system SHALL connect to `data/app.db` (SQLite)
- **AND** create all required tables if they do not exist
- **AND** fall back to in-memory mode only if SQLite connection fails

#### Scenario: Ticket CRUD via SQLite
- **WHEN** `DBQueryTool.save_ticket()` is called
- **THEN** the ticket SHALL be persisted to the `tickets` table
- **AND** `DBQueryTool.get_ticket()` SHALL query SQLite and return the record

### Requirement: Checkpoints table supports fault recovery
The system SHALL maintain a `checkpoints` table that stores serialized TicketState for active tickets.

#### Scenario: Checkpoint saved after node execution
- **WHEN** a LangGraph node completes
- **THEN** the system SHALL insert or replace a checkpoint row with the current state
- **AND** the row SHALL include `ticket_id`, `state_json`, `created_at`, and `expires_at`

#### Scenario: Expired checkpoints automatically cleaned
- **WHEN** a checkpoint's `expires_at` timestamp is in the past
- **THEN** the system SHALL exclude it from recovery queries
- **AND** a background task SHALL delete expired checkpoints daily

### Requirement: Users table stores profiles and aggregates
The system SHALL maintain a `users` table for user profiles and ticket statistics.

#### Scenario: User profile created on first ticket
- **WHEN** a ticket is received for a user_id not in the `users` table
- **THEN** the system SHALL create a default user profile with `total_tickets = 0`

#### Scenario: User stats updated on ticket completion
- **WHEN** a ticket completes
- **THEN** the system SHALL increment `users.total_tickets`
- **AND** update `users.last_contact` to the current timestamp
- **AND** recalculate `users.avg_satisfaction` from all feedback

### Requirement: Patterns table stores reusable solution templates
The system SHALL maintain a `patterns` table for common ticket categories and their solution templates.

#### Scenario: Pattern retrieved by category
- **WHEN** ProcessorAgent requests a pattern for a given category
- **THEN** the system SHALL return the most frequently used successful pattern for that category
- **AND** include its `success_rate` and `usage_count`

#### Scenario: Pattern usage tracked
- **WHEN** a pattern is used as a fallback or reference
- **THEN** the system SHALL increment its `usage_count`
- **AND** update its `success_rate` based on the ticket's final review score
