## ADDED Requirements

### Requirement: Messages managed with sliding window
The system SHALL apply a sliding window to conversation messages, retaining only the most recent N rounds when the total exceeds a configurable threshold.

#### Scenario: Messages within limit preserved intact
- **WHEN** the message count is at or below the `MAX_MESSAGES` threshold (default 20)
- **THEN** all messages SHALL be preserved without modification

#### Scenario: Messages exceeding limit trimmed
- **WHEN** the message count exceeds `MAX_MESSAGES`
- **THEN** the system SHALL retain system prompt messages
- **AND** retain the most recent 10 messages
- **AND** replace the middle portion with a summary placeholder

### Requirement: Context summary generated when window slides
The system SHALL generate a concise summary of dropped messages when the sliding window discards historical context.

#### Scenario: Summary creation on trim
- **WHEN** messages are trimmed by the sliding window
- **THEN** the system SHALL invoke a lightweight LLM call to summarize the dropped messages
- **AND** the summary SHALL be injected as a system message before the retained recent messages
- **AND** the summary SHALL NOT exceed 200 tokens

#### Scenario: Summary preserves key facts
- **WHEN** a summary is generated from dropped messages
- **THEN** it SHALL include key facts such as user identity, ticket classification, and critical tool results
- **AND** it SHALL NOT omit information required for correct resolution

### Requirement: Critical information extracted to dedicated fields
The system SHALL extract critical information from the conversation into dedicated TicketState fields to reduce dependency on full message history.

#### Scenario: Key facts stored in state
- **WHEN** a ticket is classified or processed
- **THEN** key facts (category, priority, user_id, vip_status) SHALL be stored in top-level TicketState fields
- **AND** Agent prompts SHALL reference these fields directly rather than requiring the model to infer them from message history
