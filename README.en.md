> [中文文档](README.md) · English

<p align="center">
  <img src="docs/images/logo.png" alt="Talk2Code Logo" width="120" />
</p>

<h1 align="center">Talk2Code</h1>

<p align="center">
  <b>Turn one sentence into a runnable web app.</b>
</p>

<p align="center">
  Describe a requirement in natural language → a team of AI agents (Product Manager / Coder / QA) collaborates → you get runnable, downloadable product code, verified in a real browser.
</p>

<p align="center">
  <a href="https://github.com/WowCoder/talk2code/actions/workflows/ci.yml"><img src="https://github.com/WowCoder/talk2code/actions/workflows/ci.yml/badge.svg" alt="Build"></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/LangGraph-1.x-005571" alt="LangGraph">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
</p>

<p align="center">
  <img src="docs/talk2code_pitch.gif" alt="Talk2Code demo: generate a Snake game from one sentence" width="100%" />
</p>

<p align="center">
  🎬 <a href="https://github.com/WowCoder/talk2code/blob/main/docs/talk2code_pitch.mp4">Watch the full video (MP4)</a>
  &nbsp;·&nbsp; 📐 <a href="#architecture">Architecture</a>
  &nbsp;·&nbsp; ⚡ <a href="#quick-start">Quick Start</a>
</p>

**The one-line pitch:**
- 🗣️ **Plain language in, working app out** — one requirement → multi-agent collaboration → runnable, downloadable app
- 🤖 **Three roles, clear division of labor** — Tech Lead plans, Engineer codes, QA verifies on a real device
- ✅ **Real acceptance, not theater** — Playwright runs your acceptance criteria in a real browser, line by line
- 🚦 **Delivery gate** — critical defects must hit zero before anything ships; no "it runs, ship it"
- 🔁 **Gets smarter over time** — 21 regression tasks + cross-session memory, so it stops repeating past mistakes

## 📑 Table of Contents

- [Why Talk2Code is different](#why-talk2code-is-different)
- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Core Features](#core-features)
- [Quality Assurance (eval + CI gate)](#quality-assurance-eval--ci-gate)
- [Roadmap](#roadmap)
- [License](#license)
- [Contributing](#contributing)

---

## Why Talk2Code is different

Most "AI writes code" tools stop at dumping a pile of fragments. Talk2Code treats **engineering quality** as a first-class citizen:

| Capability | How |
|------------|-----|
| 🧠 Multi-agent division of labor | LangGraph orchestrates Tech Lead / Engineer / QA with separated responsibilities — not one-shot generation |
| ✅ Real acceptance | QA executes your ACs in headless Chromium with Playwright — not "looks about right" |
| 🛡️ Delivery gate | Critical defects must reach zero, otherwise it transitions to `needs_user_input` with a diff report |
| 🔗 Cross-file contracts | Plan-time export declarations → code-time contract injection → acceptance-time closure check, killing "silent button failures" |
| 🚫 Write-time interception | PRE_WRITE Hook blocks sandbox-breaking patterns (`type="module"` / external CDN) at zero LLM cost |
| 🔁 Automatic defect recycling | Failed acceptance routes back to Coder by defect category, with a root-cause card for architecture issues, then re-verified |
| 📊 Regression discipline | 21 pinned regression tasks (incl. a Snake-game failure-mode suite); baseline diff before/after core changes; failures feed the memory store |

## Tech Stack

- **Frontend**: Vue 3 + TypeScript + Vite
- **Backend**: Python 3.11+ + Flask + LangGraph
- **Database**: PostgreSQL + pgvector (one `docker compose up -d` to start); auto-falls back to SQLite when `DATABASE_URL` is unset
- **Realtime**: SSE (Server-Sent Events)
- **Auth**: JWT
- **Async tasks**: Celery + Redis (requirement generation is async, non-blocking)
- **AI models**: OpenAI / Anthropic protocol compatible, config-driven provider switching
- **Vector retrieval**: BGE-M3 hybrid retrieval

## Architecture

<p align="center">
  <img src="docs/architecture.svg" alt="Talk2Code architecture flow" width="100%" />
</p>

## Project Structure

```
talk2code/
├── backend/
│   ├── app.py                    # Entry point (assemble factory.app + register route blueprints)
│   ├── factory.py                # Flask app factory (app / CORS / JWT / rate-limit / SSE + task queue assembly)
│   ├── config.py                 # Config management
│   ├── routes/                   # API route blueprints (auth / requirements / preview / health)
│   ├── celery_app.py             # Celery async task definitions
│   ├── models/                   # Data models
│   ├── llm/                      # Unified LLM client (OpenAI / Anthropic dual protocol)
│   ├── harness/                  # Agent runtime framework
│   │   ├── instructions/         # LLM instructions & prompt management
│   │   │   ├── compactor.py     # Context compaction (preserve markers protect key messages)
│   │   │   ├── skill_loader.py  # Declarative skill loading (manifest.json trigger, knowledge/workflow types)
│   │   │   ├── prompts/skills/  # 9 skills: 6 knowledge (generic/color/typography/accessibility/anti-ai-slop/game)
│   │   │   │                    #   + 3 workflow (scaffold / refactor / code_review, callable & composable via run_skill)
│   │   │   └── nodes.py         # LangGraph nodes (supports agent delegation)
│   │   ├── tools/                # Tool registry (ToolHandler + @register_tool decorator)
│   │   │   └── skill_tools.py    # run_skill: entry point for agents to call / compose workflow skills
│   │   ├── state/                # State management / workspace / memory system
│   │   ├── constraints/          # Hooks & quality constraints
│   │   ├── events.py             # Typed event models (Pydantic)
│   │   ├── plugins/              # Plugin system (.talk2code-plugin/plugin.json)
│   │   └── observability/        # Tracing / token cost / SSE reporting
│   ├── services/                 # SSE transport, task queue
│   └── utils/                    # Rate limiting, retry, security utilities
├── frontend-vue/                 # Vue 3 + TypeScript frontend
│   └── src/
│       ├── components/           # UI components
│       ├── views/                # Page views
│       ├── composables/          # Composables
│       ├── stores/               # Pinia state
│       └── router/               # Router config
├── start.sh                      # One-click launcher
└── openspec/                     # OpenSpec spec-driven development
```

## Quick Start

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
```

### 2. Configure your LLM

Copy the template and fill in your API key:

```bash
cp backend/.env.example backend/.env
# edit backend/.env and set LLM_API_KEY
```

Two protocols, switched via `LLM_PROVIDER`:

| Protocol | Works with |
|----------|------------|
| `openai_compatible` | DeepSeek, DashScope, OpenAI, Zhipu, Moonshot, etc. |
| `anthropic_compatible` | Anthropic Claude, etc. |

```bash
# OpenAI-compatible example (DeepSeek)
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_API_KEY=your-api-key-here
```

### 3. Launch

**Option A — one click:**

```bash
./start.sh
```

**Option B — manual:**

```bash
# Build frontend
cd frontend-vue && npm install && npm run build && cd ..

# Start backend
cd backend && python app.py
```

Open http://localhost:5001

### 4. Demo account

- Username: `test`
- Password: `123456`

## Core Features

### Multi-agent collaboration

A 3-node agent workflow orchestrated by LangGraph (v1.x, currently 1.1.10), fronted by an intent classifier for smart routing.

| Agent | Role | Responsibility |
|-------|------|----------------|
| **TeamLeader** | Tech Lead | Requirement analysis → structured Plan → complexity grading (simple / standard) |
| **Coder** | Engineer | Batch file creation + adaptive iteration cap (file-count driven); `write_file` returns a content preview to avoid re-reads |
| **QA** | Quality Engineer | Playwright real-browser AC verification → fast path (skip LLM eval when all pass) |

**Two complexity SOPs**, auto-selected by the Tech Lead:

| Level | Trigger | Flow |
|-------|---------|------|
| 🟢 **simple** | Single HTML page, minimal interaction | Tech Lead light analysis → Engineer 5-round fast path → `run_preview` verify → done |
| 🔵 **standard** | Multi-file, interactive app | Tech Lead full Plan + AC (programmatic DoD) → user confirm → Engineer batch create (file-count driven rounds) → QA AC verification → route fixes by defect category → PASS / needs_user_input (delivery gate) |

The full engineering-quality system is detailed in [Architecture & Design](docs/ARCHITECTURE.md), including:

- **Deterministic acceptance**: L0 environment contract (write-time interception) / L2 deep evaluation / L3 interactive acceptance
- **Delivery gate** with defect auto-recycling by category
- **Cross-file API contracts** (export closure, no silent button failures)
- **Plan DoD validation** and context-efficiency optimization
- **Memory system** + 21 regression tasks forming a **learning loop**

## Quality Assurance (eval + CI gate)

`eval/tasks/tasks.yaml` pins 21 regression tasks covering real past failures (ES Module / CORS, CDN sandbox, cross-file export breaks, the Snake-game seven-loss streak, etc.).

Standard command (run from `backend/`, venv at repo root `venv/`):

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
    -u ALL_PROXY -u all_proxy PYTHONPATH=. \
    ../venv/bin/python ../eval/run_eval.py --no-preview
```

- `--no-preview` skips Playwright browser acceptance (file / structure / content assertions only) — fast and avoids 429s
- Drop the flag to enable **preview runtime verification**: headless Chromium actually loads the generated page and captures `pageerror` / `console_error` / `request_failed`, complementing the static audit for "static + runtime" double coverage
- eval is a standalone process and must explicitly clear proxy vars, otherwise it inherits system proxies and hits `ProxyError` on LLM calls (same root cause as production req #134)

**CI quality gate**: `.github/workflows/eval.yml` runs the full eval on every PR to `main`, comparing against `eval/baseline_golden.json` (20/21 = 95.2%). A pass rate below baseline fails the gate. Requires `LLM_API_KEY` in GitHub Secrets.

## Roadmap

- More agent roles and dedicated tools (database / API integration agents)
- Multi-turn conversational iteration and "human-in-the-loop" fine-tuning
- Enterprise deployment: horizontal scaling, auth, and audit
- More frontend framework templates and component-library skills

## License

Released under the [MIT License](LICENSE).

## Contributing

Issues and PRs are welcome. Before touching core harness code, run the regression baseline (`eval/run_eval.py --no-preview`) for a before/after diff. See [Architecture & Design](docs/ARCHITECTURE.md) and [OpenSpec](openspec/).

---

> 🌐 中文文档：[README.md](README.md)
