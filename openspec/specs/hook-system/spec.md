# hook-system Specification

## Purpose
TBD - created by archiving change harness-6layer-refactor. Update Purpose after archive.
## Requirements
### Requirement: HookManager orchestrates lifecycle hooks

The system SHALL provide a `HookManager` that supports registering and triggering hooks at 6 lifecycle points: PRE_TOOL_USE, POST_TOOL_USE, PRE_LLM_CALL, POST_LLM_CALL, ON_ERROR, ON_TASK_COMPLETE.

#### Scenario: Pre-tool-use hook validates before execution
- **WHEN** a tool is about to be executed
- **THEN** all PRE_TOOL_USE hooks SHALL run before the tool handler is invoked
- **AND** if any hook returns a failure, the tool MAY be blocked

#### Scenario: Post-tool-use hook validates results silently
- **WHEN** a tool completes successfully and all POST_TOOL_USE hooks pass
- **THEN** no hook content SHALL be added to the Agent conversation context (silent success)

#### Scenario: Post-tool-use hook failure is fed back to Agent
- **WHEN** a POST_TOOL_USE hook returns a failure message
- **THEN** the failure SHALL be appended to the Agent's dialogue history as a system message
- **AND** the Agent SHALL be prompted to fix the issue

#### Scenario: ON_TASK_COMPLETE triggers memory extraction
- **WHEN** the tool loop ends with task complete
- **THEN** MemoryStore.extract_memories() SHALL be called as part of ON_TASK_COMPLETE hooks

### Requirement: Quality hooks validate code correctness

The system SHALL provide quality assurance hooks that check HTML validity, CSS syntax, JS syntax, and required file presence after each write_file operation.

#### Scenario: HTML syntax error is caught and reported
- **WHEN** Agent writes an HTML file with malformed tags
- **THEN** the HTML validity hook SHALL return a failure message with the specific error
- **AND** the Agent SHALL receive this as a system message to fix

#### Scenario: JS syntax error is caught via Node.js check
- **WHEN** Agent writes a JS file with syntax errors
- **THEN** the `js_syntax_hook` SHALL run `node --check` and return the error message

#### Scenario: Missing index.html is caught at task completion
- **WHEN** ON_TASK_COMPLETE triggers and no `index.html` exists
- **THEN** `required_files_hook` SHALL return a failure

### Requirement: Security hooks detect common vulnerabilities

The system SHALL provide security hooks that detect XSS risks (innerHTML, document.write, eval, data: URIs) in generated code.

#### Scenario: innerHTML usage is flagged
- **WHEN** Agent writes JavaScript containing `innerHTML =`
- **THEN** the XSS hook SHALL return a warning suggesting `textContent` or `createElement`

#### Scenario: eval usage is flagged
- **WHEN** Agent writes JavaScript containing `eval(`
- **THEN** the XSS hook SHALL return a warning about eval security risks

### Requirement: Craft enforcer hooks make design rules mandatory

The system SHALL provide Craft enforcer hooks that convert key design quality rules from advisory prompt injection into mandatory post-generation checks.

#### Scenario: AI slop patterns are detected
- **WHEN** generated code contains "lorem ipsum", "TODO: implement", or "add your code here"
- **THEN** the `anti_ai_slop_hook` SHALL return a failure

#### Scenario: Clean code passes all checks
- **WHEN** generated code contains no violations
- **THEN** all Craft enforcer hooks SHALL return None (silent success)

### Requirement: Constraint failures have escalation strategy

The system SHALL escalate constraint failures: 1st failure → feedback to Agent for fix, 2nd failure → feedback with simplified fix suggestion, 3rd failure → skip the hook and log warning.

#### Scenario: Agent fixes issue on first attempt
- **WHEN** a Hook fails and the failure is fed back to Agent
- **AND** Agent regenerates the file correctly
- **THEN** the next Hook check SHALL pass silently

#### Scenario: Repeated failure is skipped
- **WHEN** the same Hook fails 3 times for the same issue
- **THEN** the Hook SHALL be skipped for the remainder of this task
- **AND** a warning SHALL be logged

