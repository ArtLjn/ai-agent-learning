## ADDED Requirements

### Requirement: Operations user can manage account roles and status
The system SHALL allow authorized operations users to view accounts and update role or status.

#### Scenario: Disable account
- **WHEN** an operations user disables an account
- **THEN** the system MUST prevent the disabled account from accessing protected business APIs

#### Scenario: Change account role
- **WHEN** an operations user changes a role between employee, service desk, and operations roles
- **THEN** the system MUST persist the new role and record the operation in audit logs

### Requirement: Operations user can monitor workflow execution
The system SHALL provide workflow monitoring by state machine topology and ticket trace.

#### Scenario: View state machine canvas
- **WHEN** an operations user opens the workflow canvas
- **THEN** the system MUST show workflow nodes, edges, human handoff points, retry paths, and completion paths

#### Scenario: View ticket trace details
- **WHEN** an operations user opens a ticket trace
- **THEN** the system MUST show span hierarchy, node input, node output, decision metadata, duration, and error information when available

### Requirement: Operations user can debug intelligent strategies
The system SHALL provide Prompt, RAG, and Agent statistics tools for strategy debugging.

#### Scenario: Activate prompt version
- **WHEN** an operations user activates a Prompt version for an Agent
- **THEN** the system MUST make that version active for subsequent Agent calls and record the change

#### Scenario: Run RAG debug query
- **WHEN** an operations user submits a RAG debug query
- **THEN** the system MUST call rag-service and display retrieval results, rerank results, scores, and errors if the service is unavailable

#### Scenario: View Agent call statistics
- **WHEN** an operations user opens Agent statistics
- **THEN** the system MUST aggregate call count, average duration, success rate, and error rate by Agent and time range

### Requirement: Operations user can inspect system health and cost
The system SHALL show system-level health and Token usage for the intelligent ticket system.

#### Scenario: View dependency health
- **WHEN** an operations user opens health status
- **THEN** the system MUST show status and latency for rag-service, Qdrant, LLM, and Embedding components

#### Scenario: View Token cost summary
- **WHEN** an operations user opens Token statistics
- **THEN** the system MUST show system-level token usage grouped by date, model, and call type without per-user billing or quota controls
