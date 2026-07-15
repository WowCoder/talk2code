## ADDED Requirements

### Requirement: Clarify questions include visual style preference

The system SHALL extend the existing `_generate_clarify_questions()` prompt to include a visual style question when the requirement involves UI/pages/interfaces, without changing the SSE protocol or frontend form component.

#### Scenario: UI requirement includes visual style question
- **WHEN** a vague requirement involves UI elements (页面、界面、按钮、表单)
- **THEN** the generated question form SHALL include a radio question for visual style preference
- **AND** options SHALL include: 极简白, 暖柔风格, 暗黑科技, 活泼多彩, 无偏好

#### Scenario: Non-UI requirement skips style question
- **WHEN** a vague requirement does NOT involve UI (e.g., "做一个小工具")
- **THEN** the generated question form SHALL NOT include a visual style question

### Requirement: Style preference is injected into Coder prompt

The system SHALL append user-selected visual style preferences to the requirement content as `[用户补充说明]\n视觉风格偏好: xxx`, which is then injected into the Coder's system prompt as a design constraint.

#### Scenario: Warm Soft style is injected as design constraint
- **WHEN** user selects "暖柔风格" in the clarify form
- **THEN** the Coder system prompt SHALL include: "视觉风格: 暖柔风格 — 暖色调、OKLch色彩空间、圆角卡片、柔和阴影"
- **AND** this constraint SHALL take priority over default Craft rules

#### Scenario: User skips style selection
- **WHEN** user clicks "跳过，使用默认风格"
- **THEN** the default Warm Soft style SHALL be applied
- **AND** no additional style question SHALL appear in subsequent interactions

### Requirement: Design preference discovery reuses existing clarify infrastructure

The system SHALL implement the visual style discovery entirely by extending the existing `_generate_clarify_questions()` LLM prompt, reusing the SSE `question-form` event, frontend form component, and `POST /api/requirements/<id>/clarify` endpoint without any new endpoints or frontend components.

#### Scenario: Clarify flow includes style without infrastructure changes
- **WHEN** Planner detects a vague UI requirement
- **THEN** the same SSE `question-form` event SHALL be used
- **AND** the same frontend radio/text form SHALL render the style question
- **AND** the same `POST /clarify` endpoint SHALL receive the style answer
