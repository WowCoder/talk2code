# Skill-Based Templates

## ADDED Requirements

### Requirement: Application templates SHALL be defined as Markdown Skill files

The system SHALL store application type definitions as `skills/<name>/SKILL.md` Markdown files with YAML frontmatter, replacing hardcoded Python functions. Each Skill file MUST contain: `name`, `description`, `triggers` (list of keywords for matching), and a `template` section defining the default code structure.

#### Scenario: System discovers skills at startup
- **WHEN** the application starts
- **THEN** all `skills/*/SKILL.md` files SHALL be parsed and registered as available application types

#### Scenario: New skill added without code changes
- **WHEN** a developer creates a new `skills/pomodoro/SKILL.md` directory with valid frontmatter
- **THEN** the Pomodoro timer app type SHALL be available for matching on the next request without restarting the application

### Requirement: Skill matching SHALL use keyword-based routing with LLM fallback

The system SHALL first attempt keyword matching against the user's requirement text using the `triggers` field. If no keyword matches or multiple ambiguous matches exist, the Planner node SHALL select the best matching Skill via LLM analysis.

#### Scenario: Exact keyword match routes to correct skill
- **WHEN** user submits requirement "做一个待办清单应用，支持优先级标签"
- **THEN** the system SHALL match the `todo` skill (triggers: [待办, todo, 清单, 任务])

#### Scenario: Ambiguous requirement triggers LLM-assisted matching
- **WHEN** user submits requirement "帮我做个工具" (no specific keyword match)
- **THEN** the Planner node SHALL analyze the requirement and select the most appropriate Skill, or fall back to `generic`

### Requirement: Skills SHALL declare their Craft rule requirements

Each Skill file SHALL declare which Craft categories it requires via the `craft_requires` field. The system SHALL inject only the declared Craft rules into the prompt for that Skill.

#### Scenario: Todo skill requires accessibility and anti-slop rules
- **WHEN** the `todo` skill is matched with `craft_requires: [anti-ai-slop, accessibility-baseline, typography]`
- **THEN** only those three Craft rule sets SHALL be injected into the Coder prompt (not color rules)

### Requirement: Skill fallback templates SHALL be loadable as JSON

Each Skill SHALL reference or embed a fallback code template (as JSON with filename/content pairs). When LLM generation fails, the system SHALL use this template instead of generating from scratch.

#### Scenario: LLM failure triggers skill-specific fallback
- **WHEN** the Coder node fails to generate code
- **THEN** the fallback template from the matched Skill SHALL be used, producing a functional (if basic) application matching the expected type
