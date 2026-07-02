## 1. SQLite Persistence Layer

- [ ] 1.1 Add `aiosqlite` to requirements.txt
- [ ] 1.2 Create `src/multi_agent_system/core/database.py` with SQLite async connection manager
- [ ] 1.3 Define schema: `tickets`, `users`, `checkpoints`, `patterns` tables with CREATE TABLE statements
- [ ] 1.4 Refactor `DBQueryTool` to use SQLite instead of in-memory dict (保留内存回退模式用于测试)
- [ ] 1.5 Add `AnalyticsTool` SQLite queries for category/priority distribution and resolution stats
- [ ] 1.6 Write tests for SQLite CRUD operations

## 2. Agent Memory System

- [ ] 2.1 Extend `TicketState` with `thought_chain`, `tool_history`, `user_context`, `checkpoint_id` fields
- [ ] 2.2 Create `src/multi_agent_system/core/memory.py` with `MemoryManager` class
- [ ] 2.3 Implement working memory: in-memory ReAct step tracking (thought/action/observation)
- [ ] 2.4 Implement short-term memory: checkpoint save/restore via SQLite
- [ ] 2.5 Implement long-term memory: user profile load/update, historical ticket query
- [ ] 2.6 Implement memory integration in LangGraph nodes (load at start, save after each node)
- [ ] 2.7 Add service startup recovery: query unfinished checkpoints and resume processing
- [ ] 2.8 Write tests for memory save/restore and recovery logic

## 3. ReAct ProcessorAgent

- [ ] 3.1 Create `src/multi_agent_system/core/tool_base.py` with `ToolBase` abstract class and Pydantic schema support
- [ ] 3.2 Refactor existing tools (`KnowledgeSearchTool`, `DBQueryTool`, `NotificationTool`) to inherit `ToolBase`
- [ ] 3.3 Add `ToolRegistry` for tool registration, schema export, and lookup by name
- [ ] 3.4 Implement parameter validation: Pydantic model validation with structured error feedback
- [ ] 3.5 Refactor `ProcessorAgent` to ReAct loop: `Thought -> Action -> Observation` iteration
- [ ] 3.6 Add `max_iterations=10` safeguard with graceful fallback
- [ ] 3.7 Ensure `ProcessorAgent.process()` interface backward compatibility
- [ ] 3.8 Write tests for ReAct loop, tool schema validation, and parameter error recovery

## 4. Context Management

- [ ] 4.1 Create `src/multi_agent_system/core/context_manager.py` with `ContextManager` class
- [ ] 4.2 Implement sliding window: retain system prompts + recent N messages (default 20)
- [ ] 4.3 Implement summary generation: LLM-based compression of dropped messages (max 200 tokens)
- [ ] 4.4 Integrate context manager into `ProcessorAgent` ReAct loop (trim before each LLM call)
- [ ] 4.5 Add critical info extraction: category/priority/user_id stored in dedicated state fields
- [ ] 4.6 Write tests for sliding window, summary generation, and context trimming

## 5. Agent Evaluation Framework

- [ ] 5.1 Extend metrics collection: add token_count, tool_call_count, total_duration per ticket
- [ ] 5.2 Create `src/multi_agent_system/core/evaluation.py` with `EvaluationCollector` class
- [ ] 5.3 Implement objective metrics aggregation: success_rate, avg_retries, avg_duration, avg_tokens
- [ ] 5.4 Add user feedback endpoint: `POST /api/tickets/{id}/feedback` with `satisfied: bool`
- [ ] 5.5 Integrate evaluation into workflow: record metrics on completion, update user stats
- [ ] 5.6 Update `/analytics` endpoint to include evaluation metrics
- [ ] 5.7 Write tests for metric collection and feedback processing

## 6. Integration & Polish

- [ ] 6.1 Update `app.py` lifespan: initialize SQLite connection, run migration, restore checkpoints
- [ ] 6.2 Update `config.py` with new settings (max_messages, checkpoint_ttl, etc.)
- [ ] 6.3 Update `requirements.txt` with all new dependencies
- [ ] 6.4 Update README with memory system and ReAct architecture documentation
- [ ] 6.5 Run full test suite and fix regressions
- [ ] 6.6 Verify Docker Compose deployment still works
