# Settings Page

## ADDED Requirements

### Requirement: Settings page SHALL use sidebar layout with 4 sections

The system SHALL provide a settings page at `settings.html` with a left sidebar navigation and right content area. Sections SHALL include: 个人资料 (Profile), 外观偏好 (Appearance), 账户安全 (Account), 关于 (About).

#### Scenario: User navigates settings sections via sidebar
- **WHEN** user clicks a sidebar link
- **THEN** the corresponding content section SHALL become visible and the sidebar link SHALL highlight as active

#### Scenario: Default section is Profile
- **WHEN** user first loads settings.html
- **THEN** the "个人资料" section SHALL be active by default

### Requirement: Profile section SHALL show avatar and editable fields

The system SHALL display a user avatar (initial-based circle), username input, and email input in the profile section.

#### Scenario: User updates profile
- **WHEN** user edits username or email and clicks "保存修改"
- **THEN** the changes SHALL be submitted to the backend (placeholder interaction in prototype)

### Requirement: Appearance section SHALL include toggle switches

The system SHALL provide toggle switches for dark mode and auto-save preferences, plus a font size selector for the code editor.

#### Scenario: User toggles dark mode
- **WHEN** user clicks the dark mode toggle
- **THEN** the toggle SHALL animate from off (gray) to on (accent color) state

### Requirement: Account section SHALL have password change form and delete option

The system SHALL provide a password change form (current password, new password, confirm) and a danger-zone "注销账户" button with destructive styling.

#### Scenario: User interface for dangerous operations
- **WHEN** the account section renders the "注销账户" button
- **THEN** it SHALL use red/destructive color styling (`oklch(42% 0.15 20)` text, `oklch(75% 0.08 20)` border)

### Requirement: About section SHALL display version and tech stack

The system SHALL display application version, build date, tech stack, and design system name in a key-value card layout.

#### Scenario: About section shows version info
- **WHEN** user views the About section
- **THEN** it SHALL display version "v2.0.0", build date "2026.05.20", tech stack "Flask + LangGraph + SSE", and design system "Warm Soft"
