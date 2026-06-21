## ADDED Requirements

### Requirement: Tracer provides end-to-end request tracing

The system SHALL provide a `Tracer` that creates a `Trace` for each requirement processing request, containing `Span` objects for each LangGraph node and tool call with timing, status, and metadata.

#### Scenario: Trace is created at request start
- **WHEN** `process_requirement()` begins
- **THEN** a new Trace SHALL be created with `requirement_id` and `user_id`
- **AND** the Trace SHALL be persisted to SQLite on completion

#### Scenario: Span captures node execution timing
- **WHEN** `planner_node` starts and ends
- **THEN** a Span SHALL record start_time, end_time, and status (success/error)
- **AND** metadata SHALL include input length and output plan size

#### Scenario: Tool call spans are nested under tool_executor
- **WHEN** a tool is executed within `tool_executor_node`
- **THEN** a Span SHALL be created with `parent_id` set to the executor span
- **AND** metadata SHALL include tool name, argument summary, and result status

### Requirement: CostTracker records token usage and cost

The system SHALL provide a `CostTracker` that extracts `usage` data from LLM API responses and calculates cost based on per-model pricing.

#### Scenario: Token usage is recorded after each LLM call
- **WHEN** `LLMClient.chat_with_tools()` receives a response with `usage` field
- **THEN** input_tokens and output_tokens SHALL be recorded to the trace
- **AND** cost SHALL be calculated using the model's pricing configuration

#### Scenario: Cost summary is included in trace_summary SSE event
- **WHEN** a task completes
- **THEN** a `trace_summary` SSE event SHALL be sent containing total_tokens, total_cost, model name, and total time

### Requirement: SSE event system covers all agent activities

The system SHALL extend the SSE event protocol to include: `tool_call`, `tool_result`, `thinking`, `hook_check`, `permission_request`, and `trace_summary` in addition to existing `progress`, `dialogue`, `code`, `question_form`, `complete`, and `error` events.

#### Scenario: Tool call appears as a card in the conversation panel
- **WHEN** Agent invokes a tool
- **THEN** a `tool_call` SSE event SHALL be sent with readable tool name and argument summary
- **AND** the frontend SHALL render it as a tool card in the conversation panel

#### Scenario: Thinking appears as streaming text
- **WHEN** LLM returns text content before tool calls
- **THEN** a `thinking` SSE event SHALL stream the content to the conversation panel

#### Scenario: Silence triggers heartbeat
- **WHEN** no SSE event is sent for 5 seconds during LLM call or tool execution
- **THEN** a `thinking` heartbeat event SHALL be sent: "正在思考中..." or "正在执行..."

### Requirement: Logging system writes to project logs directory

The system SHALL write structured logs to `{project_root}/logs/` with four categories: `app.log` (Flask/DB/auth), `agent.log` (Planner/Coder/ToolLoop decisions), `llm.log` (LLM request/response/usage/latency), and `access.log` (HTTP requests).

#### Scenario: Agent log records tool call with trace_id
- **WHEN** Agent executes a tool call
- **THEN** `agent.log` SHALL record: timestamp, trace_id, node_name, event type, and message

#### Scenario: LLM log records token usage and latency
- **WHEN** an LLM call completes
- **THEN** `llm.log` SHALL record: timestamp, trace_id, model, input_tokens, output_tokens, and latency

#### Scenario: Logs rotate by size and time
- **WHEN** a log file exceeds 50MB or a new day starts
- **THEN** the log SHALL rotate: current file renamed, new file created
- **AND** logs older than retention period SHALL be gzip-compressed to archive/

### Requirement: Prometheus metrics expose observability data

The system SHALL expose a `/api/metrics` endpoint with Prometheus-format metrics for request counts, agent node durations, LLM token totals, tool call counts, hook check results, and active sessions.

#### Scenario: Metrics endpoint returns current counters
- **WHEN** `GET /api/metrics` is called
- **THEN** all metric counters SHALL be returned in Prometheus text format

### Requirement: Frontend observation panel shows execution details

The system SHALL add a collapsible "执行详情" panel to `detail.html` showing the execution tree (Planner → Coder → tool calls with timing and status), token usage, cost, model info, and retry count.

#### Scenario: User expands execution details after task completion
- **WHEN** user clicks "执行详情" toggle after a task completes
- **THEN** a tree view SHALL show Planner, Coder, and each tool call with timing and ✓/✗ status
- **AND** a summary row SHALL show total tokens, cost, model, and retries
