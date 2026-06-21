# History Page

## ADDED Requirements

### Requirement: History page SHALL display all user projects in a searchable list

The system SHALL provide a history page at `history.html` listing all user's AI-generated projects. Each item SHALL show title, description excerpt, date, file count, and status badge. The page SHALL include a search input that filters items by title in real-time.

#### Scenario: User views history with multiple projects
- **WHEN** user navigates to history.html
- **THEN** all projects SHALL be displayed as cards with title, excerpt, metadata (date, file count), and a colored status badge

#### Scenario: User searches history
- **WHEN** user types in the search input
- **THEN** the list SHALL filter in real-time to show only items whose title contains the search query (case-insensitive)

### Requirement: Status badges SHALL use distinct colors per state

The system SHALL render status badges with semantic colors: green for `finished`, blue for `processing`, amber/yellow for `pending`, red for `failed`.

#### Scenario: Completed project shows green badge
- **WHEN** a project has status "已完成" (finished)
- **THEN** its badge SHALL render with green background (`oklch(93% 0.05 155)`) and green text (`oklch(38% 0.1 155)`)

#### Scenario: Failed project shows red badge
- **WHEN** a project has status "失败" (failed)
- **THEN** its badge SHALL render with red background (`oklch(94% 0.03 20)`) and red text (`oklch(42% 0.15 20)`)

### Requirement: Empty state SHALL guide user to create first project

The system SHALL display an empty state with illustration area, heading, description, and CTA button when no projects exist.

#### Scenario: First-time user sees empty state
- **WHEN** user with no projects visits history.html
- **THEN** the empty state SHALL display with a decorative icon area, "还没有项目" heading, "创建你的第一个项目，开始用自然语言生成应用" description, and a "开始创建" button linking to index.html

### Requirement: Each history item SHALL be clickable to navigate to detail page

The system SHALL make each history item a clickable link that navigates to `detail.html?id=<requirement_id>`.

#### Scenario: User clicks a history item
- **WHEN** user clicks on a completed project in the history list
- **THEN** the browser SHALL navigate to the detail page for that requirement
