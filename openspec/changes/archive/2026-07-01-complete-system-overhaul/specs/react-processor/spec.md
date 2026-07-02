## ADDED Requirements

### Requirement: ProcessorAgent uses ReAct reasoning loop
The system SHALL implement ProcessorAgent with a ReAct (Reasoning + Acting) loop that iterates through Thought, Action, and Observation steps until a solution is generated or max iterations reached.

#### Scenario: Multi-step reasoning for complex ticket
- **WHEN** ProcessorAgent receives a ticket requiring multiple information sources
- **THEN** it SHALL generate a Thought analyzing what information is needed
- **AND** select and invoke the appropriate Tool
- **AND** incorporate the Tool's Observation into the next Thought
- **AND** repeat until sufficient information is gathered

#### Scenario: Single-step resolution for simple ticket
- **WHEN** ProcessorAgent receives a ticket answerable from immediate context
- **THEN** it SHALL generate a Thought concluding no tools are needed
- **AND** produce the Final Answer in a single iteration

#### Scenario: Max iteration safeguard
- **WHEN** the ReAct loop reaches 10 iterations without producing a Final Answer
- **THEN** the system SHALL abort the loop
- **AND** return a fallback response indicating the problem requires manual review

### Requirement: Tools declare JSON Schema for parameter validation
The system SHALL require every tool to declare its parameters via a Pydantic model, from which a JSON Schema is derived for the LLM and runtime validation.

#### Scenario: Tool schema registration
- **WHEN** a tool is registered with the system
- **THEN** it SHALL provide a Pydantic model describing all parameters, types, and constraints
- **AND** the system SHALL derive a JSON Schema for LLM function calling

#### Scenario: Valid tool invocation
- **WHEN** the LLM outputs a tool call with parameters matching the schema
- **THEN** the system SHALL validate the parameters with Pydantic
- **AND** execute the tool with the validated parameters

#### Scenario: Invalid tool parameters
- **WHEN** the LLM outputs a tool call with invalid parameters (wrong type, missing required field, out of range)
- **THEN** the system SHALL reject the invocation
- **AND** return a structured error message to the LLM describing the validation failure
- **AND** the LLM SHALL receive the error as an Observation and attempt to correct the call

#### Scenario: Non-existent tool name
- **WHEN** the LLM outputs a tool call for a tool that is not registered
- **THEN** the system SHALL return an error Observation stating the tool does not exist
- **AND** the LLM SHALL adjust its approach in the next Thought

### Requirement: ProcessorAgent retains backward-compatible interface
The system SHALL ensure the external interface of ProcessorAgent remains unchanged during the ReAct refactor.

#### Scenario: Existing API calls continue to work
- **WHEN** existing code calls `processor.process(content, category, priority)`
- **THEN** the method SHALL return a dict with `result` and `references` keys
- **AND** callers SHALL NOT need to modify their invocation code
