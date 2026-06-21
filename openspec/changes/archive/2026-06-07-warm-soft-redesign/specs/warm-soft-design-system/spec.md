# Warm Soft Design System

## ADDED Requirements

### Requirement: All pages SHALL use OKLch-based CSS custom properties

The system SHALL define a consistent set of CSS custom properties on `:root` for all pages: `--bg`, `--surface`, `--fg`, `--muted`, `--border`, `--accent`, `--accent-soft`, `--font-display`, `--font-body`. Colors SHALL use OKLch color space for perceptual uniformity.

#### Scenario: Login page uses Warm Soft palette
- **WHEN** a user visits login.html
- **THEN** the background SHALL be `oklch(97% 0.018 70)` (warm beige), the accent color SHALL be `oklch(64% 0.13 28)` (terra cotta), and no purple gradients or Tailwind indigo SHALL appear

#### Scenario: Design tokens are consistent across pages
- **WHEN** navigating between login, index, detail, history, and settings pages
- **THEN** all pages SHALL share the identical `:root` variable block, ensuring visual consistency

### Requirement: Typography SHALL use serif display font for headings and system sans-serif for body

The system SHALL use `--font-display` ('Tiempos Headline', 'Newsreader', 'Iowan Old Style', Georgia, serif) for all headings and brand text. Body text SHALL use `--font-body` ('Söhne', -apple-system, BlinkMacSystemFont, system-ui, sans-serif).

#### Scenario: Hero heading renders in serif display font
- **WHEN** the index page hero heading is rendered
- **THEN** it SHALL use `font-family: var(--font-display)` with `font-weight: 600` and `letter-spacing: -0.02em`

#### Scenario: Form inputs render in system sans-serif
- **WHEN** any form input is rendered
- **THEN** it SHALL use `font-family: var(--font-body)` with `font-size: 14-15px`

### Requirement: Cards SHALL have rounded corners, subtle borders, and optional radial gradient overlay

The system SHALL use `border-radius: 16-20px` for cards, `border: 1px solid var(--border)`, and a `::before` pseudo-element with radial gradient for decorative depth where specified by the prototype.

#### Scenario: Login card renders with gradient overlay
- **WHEN** the login card is rendered
- **THEN** it SHALL have a `::before` radial gradient from `oklch(94% 0.04 45 / 25%)` to transparent at the top

#### Scenario: Input card on index page renders with bottom gradient
- **WHEN** the main input card is rendered on index.html
- **THEN** it SHALL have a `::before` radial gradient from `oklch(90% 0.04 45 / 18%)` to transparent at the bottom

### Requirement: Navigation bar SHALL be sticky with backdrop blur

The system SHALL use a sticky top navigation bar with `backdrop-filter: blur(12px)` and semi-transparent background for all authenticated pages.

#### Scenario: Nav stays visible on scroll
- **WHEN** user scrolls down on any authenticated page
- **THEN** the navigation bar SHALL remain fixed at the top with blur backdrop effect
