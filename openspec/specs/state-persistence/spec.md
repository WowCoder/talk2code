# state-persistence Specification

## Purpose
TBD - created by archiving change harness-6layer-refactor. Update Purpose after archive.
## Requirements
### Requirement: WorkspaceFS provides a real filesystem per requirement

The system SHALL provide a `WorkspaceFS` that creates a dedicated directory for each requirement under `{BASE_DIR}/{user_id}/{requirement_id}/`, supporting read, write, list, delete, and snapshot operations.

#### Scenario: Workspace is initialized from database code files
- **WHEN** `workspace.init(code_files)` is called with code files from the database
- **THEN** the workspace directory SHALL be created and populated with all code files

#### Scenario: Workspace snapshot serializes to code_files format
- **WHEN** `workspace.snapshot()` is called
- **THEN** all files in the workspace SHALL be returned as `[{filename, content}, ...]`
- **AND** `.git` directory contents SHALL be excluded

### Requirement: GitVersioning auto-commits code changes

The system SHALL version all code changes via Git in each workspace, automatically committing after every `write_file` tool call and on task completion.

#### Scenario: write_file triggers auto-commit
- **WHEN** Agent successfully calls `write_file` via the tool loop
- **THEN** a Git commit SHALL be created with message `"[tool] write_file: {filename}"`

#### Scenario: Task completion triggers final commit
- **WHEN** the tool call loop ends with task complete
- **THEN** a final commit SHALL be created with message `"[agent] task complete"`

#### Scenario: Commit history is retrievable
- **WHEN** `git.log(max_count=20)` is called
- **THEN** the last 20 commits SHALL be returned with hash, message, and timestamp

#### Scenario: Rollback restores previous state
- **WHEN** `git.rollback(commit_hash)` is called with a valid hash
- **THEN** the workspace SHALL be restored to that commit's state

### Requirement: MemoryStore provides LLM-driven long-term memory

The system SHALL provide a `MemoryStore` that uses LLM to extract memories from dialogue context after each task, stores them in SQLite, and retrieves them for future sessions via LLM-based filtering.

#### Scenario: LLM extracts memories after task completion
- **WHEN** `ON_TASK_COMPLETE` Hook triggers
- **THEN** `extract_memories()` SHALL send the recent dialogue to LLM for memory extraction
- **AND** the LLM SHALL return a JSON array of `{fact, type, importance, reason}`
- **AND** memories with importance < 0.3 SHALL be discarded

#### Scenario: Conflicting memory is updated
- **WHEN** a new memory contradicts an existing one (user changed preference)
- **THEN** the existing memory SHALL be updated with the new fact
- **AND** its importance SHALL increase by 0.1

#### Scenario: Recalled memories update access timestamp
- **WHEN** `MemoryStore.recall()` successfully retrieves memories
- **THEN** each retrieved memory's `last_accessed_at` SHALL be updated to now
- **AND** `access_count` SHALL increment by 1
- **AND** `importance` SHALL increase by 0.02 (capped at 1.0)

#### Scenario: Memories decay over time
- **WHEN** the daily decay task runs
- **THEN** each memory's importance SHALL decay by factor 0.95^(days_since_access/7)
- **AND** memories with importance < 0.1 and 30+ days without access SHALL be deleted

#### Scenario: Memory retrieval uses LLM for filtering
- **WHEN** `recall()` is called with a query and there are ≤10 active memories
- **THEN** the LLM SHALL be asked to judge which memories are relevant
- **AND** only relevant memories (top_k=5) SHALL be returned

#### Scenario: Memory retrieval uses embedding when >10 memories
- **WHEN** `recall()` is called with a query and there are >10 active memories
- **THEN** embedding-based cosine similarity SHALL pre-filter to top-10 candidates
- **AND** LLM SHALL then select the final top_k=5 from candidates

### Requirement: CheckpointManager enables interrupted task recovery

The system SHALL provide a `CheckpointManager` that persists AgentState to SQLite after each LangGraph node execution and supports resuming from the latest checkpoint.

#### Scenario: Checkpoint is saved after each node
- **WHEN** a LangGraph node completes execution
- **THEN** the full AgentState SHALL be serialized and saved as a checkpoint

#### Scenario: Interrupted task resumes from checkpoint
- **WHEN** `process_requirement()` starts and finds a non-terminal checkpoint
- **THEN** the initial state SHALL be loaded from the checkpoint instead of created fresh

