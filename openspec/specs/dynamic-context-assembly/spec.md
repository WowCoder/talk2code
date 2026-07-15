# dynamic-context-assembly Specification

## Purpose
TBD - created by archiving change harness-6layer-refactor. Update Purpose after archive.
## Requirements
### Requirement: ContextAssembler dynamically assembles LLM context by requirement type

The system SHALL provide a `ContextAssembler` that dynamically selects Craft rules, Skill knowledge, and long-term memories based on requirement features, assembling them into a unified LLM context with clear priority ordering.

#### Scenario: UI-heavy requirement includes typography and color crafts
- **WHEN** a requirement has UI features (pages, buttons, forms)
- **THEN** the assembled context SHALL include `typography` and `color` Craft rules
- **AND** context SHALL NOT include unnecessary rules

#### Scenario: Form-heavy requirement includes accessibility rules
- **WHEN** a requirement has form inputs
- **THEN** the assembled context SHALL include `accessibility-baseline` Craft rules

#### Scenario: Long-term memories are injected when relevant
- **WHEN** a user has prior preferences stored in MemoryStore
- **THEN** relevant memories SHALL be recalled via `MemoryStore.recall()` and injected into system prompt
- **AND** each memory SHALL be formatted as a one-line summary with importance weight

### Requirement: ContextCompactor prevents token budget overflow

The system SHALL provide a `ContextCompactor` that monitors assembled context token count and triggers compression when usage exceeds 85% of the model's context window budget.

#### Scenario: Context under budget passes through unchanged
- **WHEN** assembled context tokens are below 85% of budget
- **THEN** `maybe_compact()` SHALL return the messages unchanged

#### Scenario: Old dialogues are summarized when budget is exceeded
- **WHEN** context exceeds 85% of budget
- **THEN** P3 priority content (old dialogue rounds) SHALL be compressed via LLM summarization
- **AND** P0 content (system prompt, skill instructions) and P1 content (structural plan, key facts) SHALL remain intact

#### Scenario: P0/P1 content is never compressed
- **WHEN** compression is triggered multiple times
- **THEN** system prompts, skill instructions, craft rules, and the structural plan SHALL never be summarized or removed

### Requirement: Single generic Skill provides frontend development knowledge

The system SHALL provide a single generic `skills/generic/SKILL.md` that contains frontend development best practices, common pitfalls, quality checklists, and browser storage selection guidance. The existing 5 specific skills (todo/calculator/note/calendar) and all `template.json` files SHALL be removed.

#### Scenario: Generic skill injects development knowledge into Coder prompt
- **WHEN** Coder's context is assembled
- **THEN** the generic Skill body SHALL be injected into the system prompt
- **AND** it SHALL include guidance on XSS prevention, localStorage patterns, and accessibility

#### Scenario: Storage strategy guidance is available
- **WHEN** the generic skill is loaded
- **THEN** it SHALL include guidance on when to use localStorage vs IndexedDB vs Cache API

### Requirement: Craft rules are selected on demand rather than all-injected

The system SHALL replace the current "always inject all Craft rules" behavior with on-demand selection based on requirement feature analysis.

#### Scenario: Calculator requirement skips typography and color rules
- **WHEN** a requirement is for a calculator (no rich UI)
- **THEN** `_select_crafts()` SHALL return an empty or minimal set
- **AND** typography, color, and accessibility-baseline rules SHALL be skipped

#### Scenario: Content application includes anti-ai-slop rules
- **WHEN** a requirement generates text-heavy content
- **THEN** `anti-ai-slop` Craft rules SHALL be selected

