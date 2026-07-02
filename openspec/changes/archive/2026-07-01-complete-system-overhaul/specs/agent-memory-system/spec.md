## ADDED Requirements

### Requirement: Working memory tracks ReAct reasoning state
The system SHALL maintain working memory within a single ReAct execution loop, tracking the current thought, executed tool calls, and their observations.

#### Scenario: ReAct loop accumulates reasoning steps
- **WHEN** ProcessorAgent enters a ReAct loop to handle a ticket
- **THEN** working memory SHALL record each Thought, Action, and Observation in sequence
- **AND** subsequent thoughts SHALL have access to all prior steps in the loop

### Requirement: Short-term memory persists ticket-level context
The system SHALL maintain short-term memory for the duration of a ticket processing lifecycle, including conversation history, tool call history, and checkpoint identifiers.

#### Scenario: Ticket state carries memory fields
- **WHEN** a ticket is created via `create_initial_state()`
- **THEN** the TicketState SHALL include `thought_chain`, `tool_history`, `user_context`, and `checkpoint_id` fields

#### Scenario: Checkpoint saves state after each node
- **WHEN** any LangGraph node completes execution
- **THEN** the system SHALL asynchronously save the current TicketState to the `checkpoints` SQLite table
- **AND** the checkpoint SHALL include a `expires_at` timestamp 24 hours in the future

#### Scenario: Service restart recovers active tickets
- **WHEN** the FastAPI service starts
- **THEN** the system SHALL query SQLite for checkpoints with `expires_at > now()`
- **AND** resume processing for each uncompleted ticket from its checkpointed state

### Requirement: Long-term memory stores user profiles and history
The system SHALL persist user profiles, historical tickets, and common solution patterns in SQLite for cross-session retrieval.

#### Scenario: User profile loaded at ticket start
- **WHEN** a ticket is received with a `user_id`
- **THEN** the system SHALL load the user's profile from the `users` table
- **AND** inject relevant profile data (vip_level, preferred_category, total_tickets) into `TicketState.user_context`

#### Scenario: Historical tickets queried during processing
- **WHEN** ProcessorAgent needs context about a user's past issues
- **THEN** the system SHALL query the `tickets` table for the user's last 5 completed tickets
- **AND** return them as structured context to the Agent

#### Scenario: Completed ticket archives to long-term memory
- **WHEN** a ticket reaches `completed` or `failed` status
- **THEN** the system SHALL save the full ticket record to the `tickets` table
- **AND** update the `users` table with aggregated statistics

### Requirement: Semantic memory provides knowledge retrieval
The system SHALL use Qdrant vector store as semantic memory for FAQ, documentation, and solution templates.

#### Scenario: Knowledge base search during processing
- **WHEN** ProcessorAgent queries for relevant knowledge
- **THEN** the system SHALL search Qdrant using the query embedding
- **AND** return top-k matching documents with relevance scores

#### Scenario: Successful solutions indexed into semantic memory
- **WHEN** a ticket is completed with `review_score >= 0.8`
- **THEN** the system SHALL index the ticket content and solution into Qdrant as a new knowledge document
