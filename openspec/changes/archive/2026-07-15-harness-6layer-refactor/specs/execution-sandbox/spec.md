## ADDED Requirements

### Requirement: PermissionManager enforces three-level tool access control

The system SHALL provide a `PermissionManager` with three permission levels: Level 0 (read-only, auto-allow), Level 1 (write, one-time grant per session), Level 2 (execute, approval required per call).

#### Scenario: Read-only tools are auto-allowed
- **WHEN** Agent calls `read_file`, `list_files`, `search_docs`, or linting tools
- **THEN** the tool SHALL execute immediately without user approval

#### Scenario: Write tools require one-time session grant
- **WHEN** Agent first calls `write_file` in a session
- **THEN** a `permission_request` SSE event SHALL be sent to the frontend
- **AND** subsequent `write_file` calls in the same session SHALL be auto-allowed

#### Scenario: Execute tools require per-call approval
- **WHEN** Agent calls `execute_code`
- **THEN** a `permission_request` SSE event SHALL be sent for each invocation
- **AND** the tool SHALL only execute after user approval

#### Scenario: Permission request times out
- **WHEN** a permission request receives no user response within 30 seconds
- **THEN** the request SHALL be automatically denied
- **AND** the tool result SHALL indicate "permission_denied"

### Requirement: SandboxExecutor runs code in isolated subprocess

The system SHALL provide a `SandboxExecutor` that executes generated HTML/CSS/JS code in a resource-limited subprocess with file system and network isolation.

#### Scenario: Code execution completes within limits
- **WHEN** `execute_code` is called for valid HTML
- **THEN** the sandbox SHALL run the code in a temporary directory
- **AND** return any console output or errors

#### Scenario: Code execution exceeds timeout
- **WHEN** code execution takes longer than 30 seconds
- **THEN** the subprocess SHALL be terminated
- **AND** the result SHALL indicate timeout

#### Scenario: Temporary directory is cleaned up
- **WHEN** sandbox execution completes (success or failure)
- **THEN** the temporary directory SHALL be removed

### Requirement: WorkspaceFS provides user-level file system isolation

The system SHALL isolate workspace directories by `user_id` and `requirement_id`, preventing path traversal and cross-user file access.

#### Scenario: User A cannot access User B's workspace
- **WHEN** a file operation targets User B's workspace from User A's session
- **THEN** the path validation SHALL prevent access because the resolved path is outside the authorized workspace

#### Scenario: Path traversal is blocked
- **WHEN** a filename contains `../` or starts with `/`
- **THEN** `_validate()` SHALL raise `PermissionError`

#### Scenario: Subdirectories are supported
- **WHEN** a file is written with a path like `css/components/button.css`
- **THEN** parent directories SHALL be created automatically
- **AND** the file SHALL be accessible within the same workspace

### Requirement: TaskQueue prevents concurrent execution per requirement

The system SHALL prevent multiple concurrent threads from executing the same requirement, ensuring workspace file consistency.

#### Scenario: Duplicate submission is rejected
- **WHEN** a task is submitted for a `requirement_id` that is already PENDING or RUNNING
- **THEN** the submission SHALL be rejected with an error message

#### Scenario: Different requirements can run concurrently
- **WHEN** tasks are submitted for different `requirement_id` values
- **THEN** both SHALL be accepted and run in parallel (up to max_workers limit)
