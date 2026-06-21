# Interactive Discovery

## ADDED Requirements

### Requirement: System SHALL detect vague requirements and trigger clarification

The system SHALL evaluate the user's requirement before executing the full generation pipeline. If the requirement is too short (fewer than 30 characters) or lacks functional keywords (no action verbs or feature descriptions), the Planner node SHALL generate a structured question form instead of a full plan.

#### Scenario: Short requirement triggers question form
- **WHEN** user submits "做个应用" (12 characters, no functional detail)
- **THEN** the system SHALL push a `question-form` SSE event to the frontend containing clarifying questions

#### Scenario: Detailed requirement bypasses discovery
- **WHEN** user submits "开发一个待办清单应用，支持添加、删除、标记完成、按优先级排序，数据保存到 LocalStorage"
- **THEN** the system SHALL proceed directly to the normal Planner → Coder workflow

### Requirement: Question forms SHALL follow a structured JSON protocol

The system SHALL generate question forms as JSON objects with a `type: "question-form"` field and a `questions` array. Each question MUST have: `id` (unique identifier), `type` (radio/text/checkbox), `label` (the question text), and `options` (for radio/checkbox types).

#### Scenario: Planner generates a radio-type question
- **WHEN** the Planner needs to clarify the application type
- **THEN** the question form SHALL contain a radio question with at least 2 options, e.g., `{"id": "app_type", "type": "radio", "label": "你想做什么类型的应用？", "options": ["工具类", "展示类", "数据管理类"]}`

#### Scenario: Frontend renders question form as interactive UI
- **WHEN** frontend receives a `question-form` SSE event
- **THEN** the left panel SHALL render radio buttons for radio-type questions, text inputs for text-type questions, and checkboxes for checkbox-type questions, with a "提交" button

### Requirement: Clarification SHALL be limited to one round

The system SHALL allow at most one round of clarification. After the user submits answers, the system MUST proceed directly to code generation. If the submitted answers are still insufficient, the system SHALL use a generic fallback rather than asking again.

#### Scenario: User submits clarification answers
- **WHEN** user fills out the question form and clicks "提交"
- **THEN** the answers SHALL be prepended to the original requirement text and the full Planner → Coder workflow SHALL execute

#### Scenario: Max clarification rounds reached
- **WHEN** one round of clarification has already occurred
- **THEN** the system SHALL NOT generate another question form, even if the combined requirement + answers is still vague
