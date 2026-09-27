> English · [中文](README.md)

<p align="center">
  <img src="docs/images/logo.png" alt="Talk2Code Logo" width="120" />
</p>

<h1 align="center">Talk2Code</h1>

<p align="center">
  <b>One sentence in. An app that runs, ships, and gets seen.</b>
</p>

<p align="center">
  Natural-language request → multi-agent collaboration (analysis / coding / real-browser acceptance)
  → one-click publish to a dedicated subdomain → optional listing in the Creation Market.
</p>

<p align="center">
  <a href="https://wowcoder.github.io/talk2code/"><img src="https://img.shields.io/badge/Project%20Page-github.io-2EA043?logo=github" alt="Project Page"></a>
  <a href="https://github.com/WowCoder/talk2code/actions/workflows/ci.yml"><img src="https://github.com/WowCoder/talk2code/actions/workflows/ci.yml/badge.svg" alt="Build"></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/LangGraph-1.x-005571" alt="LangGraph">
  <img src="https://img.shields.io/badge/Playwright-Chromium-45ba4b" alt="Playwright">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
</p>

<p align="center">
  <img src="docs/talk2code_pitch.gif" alt="Talk2Code demo: generating a Snake game from one sentence" width="100%" />
</p>

<p align="center">
  🌐 <a href="https://wowcoder.github.io/talk2code/"><b>Project page (full demo video)</b></a>
  &nbsp;·&nbsp; 🎬 <a href="https://github.com/WowCoder/talk2code/blob/main/docs/talk2code_pitch.mp4">Watch the HD video (MP4)</a>
</p>

## ✨ Features

| | |
|---|---|
| 🗣️ **Plain words, real output** | One request → a runnable, downloadable app produced by collaborating agents |
| 🤖 **Three distinct roles** | Tech Lead plans, Developer codes, QA engineer verifies in a real browser |
| ✅ **Real acceptance** | Playwright executes each acceptance criterion — not "looks about right" |
| 🚦 **Delivery gate** | Critical defects must reach zero before release; no "it runs, ship it" |
| 🚀 **One-click publish** | Artifacts become a shareable dedicated-subdomain URL, re-verified after publishing |
| 🛒 **Creation Market** | Opt-in public listing with deduplicated visitors and weighted hotness ranking |
| 🔁 **Learns from mistakes** | 21 regression tasks + cross-session memory |

## 🛠 Tech Stack

Vue 3 + TypeScript + Vite · Python 3.11+ / Flask / LangGraph · PostgreSQL + pgvector (falls back to SQLite) · Redis + Celery · SSE streaming · JWT auth · Playwright + headless Chromium

## 🏗 Architecture

<p align="center">
  <img src="docs/architecture.svg" alt="Talk2Code architecture" width="100%" />
</p>

Three LangGraph nodes (TeamLeader → Coder → QA) form the generation pipeline, followed by one-click publishing and the market. Context pipeline, memory, runtime sandbox, and observability cut across the whole flow.

## 🚀 Quick Start

```bash
git clone https://github.com/WowCoder/talk2code.git && cd talk2code

python -m venv venv && source venv/bin/activate
pip install -r backend/requirements.txt

cp backend/.env.example backend/.env      # set LLM_API_KEY
docker compose up -d                      # optional: PG + Redis, otherwise SQLite is used

./start.sh                                # → http://localhost:5001
```

- **LLM providers**: `LLM_PROVIDER` accepts `openai_compatible` (DeepSeek / DashScope / OpenAI / Zhipu / Moonshot, …) or `anthropic_compatible`.
- **First login**: run `python manage.py demo init` to create a demo account and enter read-only demo mode, or register (requires an invite code, requestable on the login page). Write operations in demo mode are rejected server-side by default.
- **Admin console**: `python manage.py admin create-user --username <u>`, served at `/admin/login`.

## 📁 Project Layout

```
talk2code/
├── backend/
│   ├── app.py / factory.py / config.py / manage.py
│   ├── routes/          # auth / requirements / preview / market / publish / invite / admin
│   ├── models/          # users / requirements / published_* / site_* / invite_codes ...
│   ├── llm/             # unified LLM client (OpenAI / Anthropic protocols)
│   ├── harness/         # agent runtime: instructions / tools / state / constraints / observability
│   └── services/        # publish / market / notify / invite / sse / task_queue
├── frontend-vue/        # Vue 3 + TypeScript frontend (views / components / stores / composables)
├── eval/                # 21 regression tasks and the CI quality baseline
├── docker-compose.yml   # PostgreSQL (pgvector) + Redis
└── openspec/            # OpenSpec spec-driven development records
```

## 🧩 What's Inside

**Multi-agent workflow** — the Tech Lead analyzes the request and sizes it up, the Developer writes the files, and the QA engineer runs every acceptance criterion in a real browser via Playwright. Anything that fails goes back for a fix, routed by defect type. Simple requests take a fast path; complex ones get a plan and acceptance criteria first.

**One-click publish** — artifacts are stored by content hash, so republishing unchanged output writes nothing and the share URL never changes. Each site runs on its own domain, which keeps generated code from reading your main-site session. After publishing, the app is verified again against the real URL, and failures are clearly flagged rather than silently passing.

**Creation Market** — nothing is public by default: it shows up in the market only when the author chooses to publish it, and a failed post-publish verification doesn't block that. Hotness = (daily unique visitors + likes×3 + comments×2) ÷ time decay, counting each visitor once per day. Likes and comments stay on the main site; published sites carry only a small badge linking back.

**Engineering quality** — patterns that are guaranteed to break in the preview sandbox (ES Modules, external CDN scripts) are rejected before a file is written; functions referenced across files are checked against what's actually exported; requests that would need a backend get their limits stated up front, along with alternatives that can still be delivered; progress is reported per action ("creating js/app.js", "2/4 done").

Details live in [Architecture & Design](docs/ARCHITECTURE.md).

## ⚠️ Notes

1. `LLM_API_KEY` must be configured for AI features to work.
2. **JWT secret**: set a strong random `JWT_SECRET_KEY` in production. The app refuses to start with the default key while reachable from outside; only isolated demo environments may opt out via `ALLOW_INSECURE_SECRETS=true`.
3. **Reverse proxy**: do not enable `TRUST_PROXY_HEADERS=true` unless behind a trusted proxy, otherwise rate-limit IPs can be spoofed.
4. **Registration**: invite codes are required by default (single use, 7 days). Use `python manage.py invite create --count N` during private beta, or follow the request → admin approval flow.
5. **Publishing**: with an empty `PUBLISH_APEX`, publish endpoints return warnings and the UI says the link is unavailable instead of handing out a dead URL.
6. **Debugging**: `backend/logs/` holds `app` / `agent` / `llm` / `llm_traffic` (JSON Lines, 7-day rotation) / `agent_exec` (needs `AGENT_EXEC_LOG=true`; the first place to look when an agent keeps re-reading the same file without producing anything).

## ✅ Quality

`eval/tasks/tasks.yaml` pins 21 regression tasks covering real historical failure modes (ES Module/CORS, CDN sandbox, broken cross-file exports, …). On every PR to `main`, CI runs the full eval against a golden baseline (20/21 = 95.2%); a lower pass rate fails the gate.

```bash
cd backend && PYTHONPATH=. python ../eval/run_eval.py --no-preview
```

Drop `--no-preview` to enable real-browser acceptance: headless Chromium loads the generated page and captures `pageerror` / `console_error` / `request_failed`, complementing static auditing with runtime evidence.

## 🗺 Roadmap

- **Conversational iteration** — keep refining after the first result ("make the button dark", "add an export-to-Excel button"). Changes land on the existing code instead of regenerating everything, and when there's a trade-off you get the options and make the call.
- **Open skill library** — open up the 14 built-in domain skill packs and let people write their own, so generated code fits a team's stack and conventions out of the box.
- **Full-stack generation** — go beyond single-page frontends to apps with APIs and data storage: one request produces front-end and back-end code that runs.

## 📚 Documentation

| Doc | Contents |
|-----|----------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it works today: acceptance model, delivery gate, context pipeline, memory, publishing & market, sandbox, observability. **Kept in sync with the code — start here** |
| [docs/design/](docs/design/) | Design notes and rationale per module — the "why", not the current state. Details may lag behind the code |
| [openspec/](openspec/) | OpenSpec spec-driven development records |

## License

Released under the [MIT License](LICENSE).

## Contributing

Issues and PRs welcome. Before touching the core harness, run the regression baseline (`eval/run_eval.py --no-preview`) and compare before/after.
