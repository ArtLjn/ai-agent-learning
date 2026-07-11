## ADDED Requirements

### Requirement: Engine classifies and structures employee tickets
The intelligent engine SHALL transform employee service requests into structured ticket fields, category, priority, and risk labels.

#### Scenario: Structured extraction succeeds
- **WHEN** an employee submits a natural language service request
- **THEN** the engine MUST extract title, category, priority, impact, contact context, confidence, and missing fields where applicable

#### Scenario: Classification fallback is needed
- **WHEN** LLM classification output is invalid or unavailable
- **THEN** the engine MUST use keyword and rule fallback to produce a safe category, priority, and route reason

### Requirement: Engine retrieves knowledge with graceful degradation
The intelligent engine SHALL use rag-service for knowledge retrieval and continue processing when retrieval is unavailable.

#### Scenario: RAG retrieval succeeds
- **WHEN** rag-service returns retrieval and rerank results
- **THEN** the engine MUST pass the top relevant knowledge snippets to the processing Agent and record retrieval statistics

#### Scenario: RAG retrieval is unavailable
- **WHEN** rag-service times out, returns degraded health, or raises an unavailable error
- **THEN** the engine MUST continue with an empty reference list and record degraded RAG metadata

### Requirement: Engine coordinates Agent processing and quality review
The intelligent engine SHALL generate a handling result, review its quality, and route low-quality results to retry or human review.

#### Scenario: Processing result passes review
- **WHEN** ReviewerAgent scores a generated result at or above the configured threshold
- **THEN** the engine MUST route the ticket toward employee notification and completion

#### Scenario: Processing result fails review
- **WHEN** ReviewerAgent scores a result below threshold and retry count is below maximum
- **THEN** the engine MUST retry processing and increment retry count

#### Scenario: Retry limit exceeded
- **WHEN** review still fails after the maximum retry count
- **THEN** the engine MUST create a human review request for the service desk

### Requirement: Engine orchestrates human handoff and employee supplement recovery
The intelligent engine SHALL manage state transitions for human handoff, employee supplement, and workflow recovery.

#### Scenario: High-risk route
- **WHEN** a ticket is complaint, P0, high risk, or missing required business fields
- **THEN** the engine MUST route the ticket to service desk human review instead of automatic completion

#### Scenario: Employee supplement recovery
- **WHEN** an employee supplements information for a ticket in `waiting_user_input`
- **THEN** the engine MUST rebuild conversation context and resume processing from the processing node

### Requirement: Engine records explainable decisions
The intelligent engine SHALL record decision metadata for classification, route, processing, review, retry, and human handoff.

#### Scenario: Decision metadata written
- **WHEN** a workflow node makes a route or quality decision
- **THEN** the engine MUST write trigger, options, selection, and execution metadata to the corresponding trace span
