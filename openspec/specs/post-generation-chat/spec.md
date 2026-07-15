# post-generation-chat Specification

## Purpose
TBD - created by archiving change harness-6layer-refactor. Update Purpose after archive.
## Requirements
### Requirement: Chat endpoint supports post-generation code modification

The system SHALL support `POST /api/requirements/<id>/chat` for modifying generated code, reusing the Coder's ReAct `ToolCallLoop` without re-running the Planner.

#### Scenario: Post-generation chat loads existing workspace
- **WHEN** user sends a chat message to a finished requirement
- **THEN** the existing WorkspaceFS SHALL be loaded with current code files
- **AND** the Planner SHALL NOT be executed
- **AND** the Coder ReAct loop SHALL start with the existing code as context

#### Scenario: Agent reads files before modifying
- **WHEN** user asks to change a specific feature
- **THEN** Agent SHALL use `read_file` to understand current code before calling `write_file`
- **AND** modifications SHALL be committed via Git with message `"[user] chat modification #{n}"`

#### Scenario: Dialogue history is compressed for long conversations
- **WHEN** `dialogue_history` exceeds the context budget
- **THEN** `ContextCompactor.maybe_compact()` SHALL compress old dialogue rounds
- **AND** core context (plan, file list) SHALL be preserved

#### Scenario: Code modifications trigger Hook checks
- **WHEN** Agent modifies a file during chat
- **THEN** quality and security hooks SHALL run on the modified file only
- **AND** failures SHALL be fed back to Agent for fixing

### Requirement: Shared ToolCallLoop handles both initial generation and modifications

The system SHALL use the same `ToolCallLoop` implementation for both initial code generation and post-generation modifications, distinguished only by the initial AgentState.

#### Scenario: Initial generation starts with empty workspace
- **WHEN** processing a new requirement
- **THEN** `ToolCallLoop` SHALL receive an AgentState with `code_files=[]`

#### Scenario: Post-generation modification starts with existing code
- **WHEN** processing a chat request on a finished requirement
- **THEN** `ToolCallLoop` SHALL receive an AgentState with existing `code_files` and `dialogue_history`

### Requirement: Chat response includes updated file list and diffs

The system SHALL return the updated code files and a list of modified filenames in the chat API response, enabling the frontend to update the code panel accordingly.

#### Scenario: Single file modification returns updated file
- **WHEN** Agent modifies only `script.js` during chat
- **THEN** the API response SHALL include the full updated code_files array
- **AND** `updated_files` SHALL be `["script.js"]`

#### Scenario: No changes needed returns original code
- **WHEN** Agent determines no code changes are needed
- **THEN** the API response SHALL return the original code_files
- **AND** the AI response text SHALL explain why no changes were made

