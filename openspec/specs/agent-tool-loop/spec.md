# agent-tool-loop Specification

## Purpose
TBD - created by archiving change harness-6layer-refactor. Update Purpose after archive.
## Requirements
### Requirement: ToolRegistry provides centralized tool registration and discovery

The system SHALL provide a `ToolRegistry` that allows registering `ToolDefinition` instances (name, description, JSON Schema parameters, handler function, permission level) and retrieving them as LLM function-calling compatible schemas.

#### Scenario: Register and execute a tool
- **WHEN** a `ToolDefinition` is registered with name `"write_file"` and a handler function
- **THEN** `get_schemas()` SHALL return the tool in OpenAI function-calling format including `name`, `description`, and `parameters`
- **AND** `execute("write_file", {"filename": "test.html", "content": "<html>..."})` SHALL invoke the handler and return a `ToolResult`

#### Scenario: Unknown tool returns error
- **WHEN** `execute("unknown_tool", {})` is called
- **THEN** the returned `ToolResult` SHALL have `success=false` and an error message

### Requirement: File tools provide workspace file operations

The system SHALL provide file operation tools: `read_file`, `write_file`, `list_files`, `delete_file` that operate within the WorkspaceFS working directory.

#### Scenario: write_file creates a file in workspace
- **WHEN** Agent calls `write_file` with `filename="index.html"` and HTML content
- **THEN** the file SHALL be created in the requirement's workspace directory
- **AND** a `tool_result` SSE event SHALL be sent to the frontend with success status and line count

#### Scenario: list_files returns all files in workspace
- **WHEN** Agent calls `list_files`
- **THEN** all files in the workspace SHALL be returned as a list of relative paths

#### Scenario: read_file returns file content
- **WHEN** Agent calls `read_file` with `filename="existing.html"`
- **THEN** the full file content SHALL be returned

### Requirement: Code validation tools check generated code quality

The system SHALL provide code validation tools: `validate_html`, `lint_css`, `lint_js`, `execute_code` that verify generated frontend code.

#### Scenario: JS syntax lint catches errors
- **WHEN** Agent calls `lint_js` with JavaScript content containing a syntax error
- **THEN** the tool SHALL return `success=false` with the error message and line number

#### Scenario: execute_code validates HTML in sandbox
- **WHEN** Agent calls `execute_code` to verify the workspace HTML
- **THEN** the sandbox SHALL run the code and return console output or errors
- **AND** execution SHALL be limited to 30s timeout and 50MB memory

### Requirement: LLMClient supports function calling for both protocols

The system SHALL extend `LLMClient` with a `chat_with_tools()` method that sends tool definitions and handles `tool_calls` in responses, supporting both OpenAI and Anthropic protocols.

#### Scenario: LLM responds with tool calls
- **WHEN** `chat_with_tools()` is called with tool schemas and a prompt requiring file creation
- **THEN** the LLM response SHALL contain `tool_calls` with tool names and arguments
- **AND** the response text content MAY also be present (thinking before acting)

#### Scenario: LLM responds without tool calls (task complete)
- **WHEN** `chat_with_tools()` is called and the LLM determines no tools are needed
- **THEN** the response SHALL have `tool_calls=None` and contain only text content
- **AND** the Agent loop SHALL interpret this as task completion

#### Scenario: Model does not support function calling
- **WHEN** `chat_with_tools()` detects the configured model does not support tools
- **THEN** the client SHALL fall back to non-tool `chat()` mode
- **AND** log a warning

### Requirement: ReAct tool call loop drives iterative code generation

The system SHALL implement a `ToolCallLoop` that iterates: call LLM → execute tool calls → observe results → repeat until the LLM returns no tool calls, up to a maximum of 10 iterations.

#### Scenario: Agent generates code through multiple tool iterations
- **WHEN** Agent needs to create a web app
- **THEN** it SHALL call LLM → receive tool_calls for write_file → execute them → send results back → continue until the LLM signals completion
- **AND** each iteration SHALL push `thinking`, `tool_call`, and `tool_result` SSE events

#### Scenario: Agent fixes errors discovered through tools
- **WHEN** `execute_code` returns a JS syntax error
- **THEN** the error SHALL be fed back to LLM in the next iteration
- **AND** the LLM SHALL attempt to fix the error by calling `write_file` again

#### Scenario: Maximum iterations reached
- **WHEN** the tool loop reaches 10 iterations without task completion
- **THEN** the loop SHALL terminate with `current_step="max_iterations"`
- **AND** the current code state SHALL be saved

#### Scenario: Three consecutive rounds with no progress
- **WHEN** the agent makes no file changes for 3 consecutive iterations
- **THEN** the loop SHALL terminate to prevent infinite loops

### Requirement: LangGraph workflow includes tool nodes

The system SHALL restructure the LangGraph workflow from `planner → coder → END` to `planner → tool_coder ↔ tool_executor → END` with conditional edges for tool use routing.

#### Scenario: Tool coder node routes to executor when tools are needed
- **WHEN** `tool_coder_node` returns a response with `tool_calls`
- **THEN** the workflow SHALL route to `tool_executor_node`
- **AND** after execution, route back to `tool_coder_node`

#### Scenario: Tool coder node routes to END when task is complete
- **WHEN** `tool_coder_node` returns a response without `tool_calls`
- **THEN** the workflow SHALL route to `END`

### Requirement: Web tools provide documentation lookup and CDN info

The system SHALL provide `search_docs` and `fetch_cdn_library` tools for accessing web documentation and CDN metadata.

#### Scenario: search_docs returns API compatibility information
- **WHEN** Agent calls `search_docs` with a query like "Array.indexOf browser support"
- **THEN** the tool SHALL return relevant MDN/CanIUse documentation summary

