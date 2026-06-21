# Craft Quality Rules

## ADDED Requirements

### Requirement: Anti-AI-slop rules SHALL be injected into code generation prompt

The system SHALL inject anti-AI-slop design rules into the ENGINEER_PROMPT system message. These rules MUST prevent common AI-generated design patterns including: excessive rounded corners, purple gradients without purpose, decorative SVG icons with no semantic meaning, over-engineered scrollbars, and cookie-cutter card designs.

#### Scenario: AI generates code without slop patterns
- **WHEN** the Coder node generates HTML/CSS code for any application type
- **THEN** the generated code SHALL NOT contain: `border-radius: 9999px` on interactive elements, purple-to-indigo gradients as primary brand color, decorative `<svg>` icons that serve no functional purpose, or animated scrollbars

#### Scenario: Craft rules are configurable
- **WHEN** the `LLM_CRAFT_ENABLED` config is set to `false`
- **THEN** the anti-slop rules SHALL NOT be injected into the prompt

### Requirement: Accessibility baseline rules SHALL be injected into code generation prompt

The system SHALL inject accessibility rules covering: minimum color contrast ratios (4.5:1 for text, 3:1 for large text), keyboard navigation support (focus-visible styles, tabindex management), ARIA labels for interactive elements, and semantic HTML hierarchy.

#### Scenario: Generated forms are keyboard accessible
- **WHEN** the Coder node generates a form component
- **THEN** all input elements SHALL have associated `<label>` elements, focus styles SHALL be visible, and the tab order SHALL follow visual layout

#### Scenario: Generated elements meet contrast requirements
- **WHEN** the Coder node generates text on colored backgrounds
- **THEN** the Tailwind classes used SHALL ensure WCAG AA contrast ratios (e.g., `text-gray-900` on `bg-white`, never `text-gray-300` on `bg-gray-100`)

### Requirement: Typography rules SHALL be injected into code generation prompt

The system SHALL inject typography rules covering: font size hierarchy (max 5 levels), line height ranges (1.5-1.75 for body, 1.2-1.3 for headings), maximum line width (75ch for prose), and font family fallback stacks.

#### Scenario: Generated text has readable typography
- **WHEN** the Coder node generates text content
- **THEN** body text SHALL have `leading-relaxed` or equivalent (1.5-1.75 line-height), headings SHALL use appropriate size progression (`text-2xl` to `text-5xl`), and prose containers SHALL use `max-w-prose` or `max-w-3xl`

### Requirement: Color system rules SHALL be injected into code generation prompt

The system SHALL inject color system rules covering: semantic color tokens (primary, success, warning, danger, neutral), limited palette size (max 4 accent colors + neutrals), and dark mode compatibility considerations.

#### Scenario: Generated UI uses semantic color tokens
- **WHEN** the Coder node generates interactive UI elements
- **THEN** success states SHALL use green tones (`emerald` or `green`), danger states SHALL use red tones (`red` or `rose`), and primary actions SHALL use a consistent accent color throughout the application
