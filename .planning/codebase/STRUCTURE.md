# Structure: Talk2Code

**Analyzed:** 2026-04-16

## Directory Layout

```
talk2code/
├── backend/
│   ├── app.py                      # Flask main application
│   ├── config.py                   # Configuration (Pydantic)
│   ├── models.py                   # Database initialization
│   ├── prompts.py                  # Prompt templates
│   ├── diff_utils.py               # Diff parsing utilities
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── state.py                # AgentState TypedDict
│   │   ├── nodes.py                # Agent node functions
│   │   └── workflow.py             # LangGraph StateGraph
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   └── client.py               # DashScope LLM client
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── models.py               # User model
│   │   └── schema.py               # Schema definitions
│   │
│   ├── routes/
│   │   └── __init__.py
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── sse_manager.py          # SSE manager
│   │   ├── task_queue.py           # Background task queue
│   │   └── requirement_service.py  # Requirement processing
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── logger.py               # Logging setup
│   │   ├── sse.py                  # SSE message formatting
│   │   ├── retry.py                # Retry utilities
│   │   ├── rate_limiter.py         # Rate limiting
│   │   ├── security.py             # Password hashing
│   │   ├── time_utils.py           # Timestamp utilities
│   │   └── time_utils.py           # Time helpers
│   │
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py             # Pytest fixtures
│   │   ├── unit/
│   │   │   ├── __init__.py
│   │   │   ├── test_llm_client.py
│   │   │   ├── test_diff_utils.py
│   │   │   ├── test_security.py
│   │   │   └── test_health.py
│   │   └── integration/
│   │       └── __init__.py
│   │
│   └── logs/                       # Log files (created at runtime)
│
├── frontend/
│   ├── login.html                  # Login/Register page
│   ├── index.html                  # Home page (requirement input)
│   └── detail.html                 # Requirement detail page
│
├── .planning/
│   └── codebase/                   # This documentation
│
├── .claude/
│   ├── commands/gsd/               # GSD command definitions
│   └── get-shit-done/              # GSD workflow definitions
│
├── CLAUDE.md                       # Claude Code project guide
├── README.md                       # Project documentation
└── .git/
```

## Key Locations

### Core Application
| File | Purpose |
|------|---------|
| `backend/app.py` | Main Flask app, all routes |
| `backend/config.py` | Configuration management |
| `backend/models.py` | DB initialization |

### AI Agent System
| File | Purpose |
|------|---------|
| `backend/agents/workflow.py` | LangGraph StateGraph |
| `backend/agents/nodes.py` | Agent implementations |
| `backend/agents/state.py` | State definition |
| `backend/llm/client.py` | LLM API client |
| `backend/prompts.py` | System prompts |

### Services
| File | Purpose |
|------|---------|
| `backend/services/sse_manager.py` | SSE connections |
| `backend/services/task_queue.py` | Background jobs |
| `backend/services/requirement_service.py` | Workflow integration |

### Utilities
| File | Purpose |
|------|---------|
| `backend/utils/sse.py` | SSE formatting |
| `backend/utils/security.py` | Password hashing |
| `backend/utils/rate_limiter.py` | Rate limiting |
| `backend/utils/retry.py` | Retry logic |
| `backend/utils/logger.py` | Logging setup |
| `backend/diff_utils.py` | Diff parsing/validation |

## File Types

### Python Modules
- `__init__.py` files mark package directories
- Most modules are single-responsibility

### Frontend
- Pure HTML/CSS/JS (no build step)
- Tailwind CSS via CDN
- CodeMirror via CDN

### Configuration
- `.env` - Environment variables (not committed)
- `.env.example` - Template (if exists)

## Naming Conventions

### Python Files
- **Modules:** snake_case (`rate_limiter.py`, `sse_manager.py`)
- **Tests:** `test_<module>.py` (`test_llm_client.py`)
- **Packages:** lowercase (`agents/`, `services/`)

### Functions
- **snake_case:** `process_requirement_async()`, `setup_logger()`

### Classes
- **PascalCase:** `Settings`, `SSEManager`, `BailianLLM`

### Variables
- **snake_case:** `dashscope_api_key`, `current_user_id`
- **Constants:** UPPER_SNAKE_CASE (`JWT_SECRET_KEY`, `RATE_LIMITS`)

### HTML Files
- **kebab-case:** `login.html`, `detail.html`

### Database
- **Tables:** snake_case plural (`users`, `requirements`)
- **Columns:** snake_case (`create_time`, `password_hash`)

## Module Organization

### By Layer
```
routes/     → HTTP endpoints
services/   → Business logic
agents/     → AI workflow
models/     → Data layer
utils/      → Shared utilities
```

### By Feature (partial)
```
llm/        → LLM integration
sse/        → Real-time features
auth        → (in app.py and utils/security.py)
```

## Import Patterns

### Absolute imports (preferred)
```python
from services.sse_manager import sse_manager
from utils.logger import get_logger
```

### Relative imports (in packages)
```python
from .state import AgentState
```

### Circular dependency handling
Local imports inside functions:
```python
def handler():
    from models import User
```

## Code Organization Principles

1. **Single responsibility per file** - Each module has one purpose
2. **Flat structure** - Minimal nesting, most files at root of package
3. **Explicit exports** - `__init__.py` files define public API
4. **Separation of concerns** - Routes, services, agents in separate directories

## Files Not Following Conventions

- `backend/models.py` (root) vs `backend/models/models.py` - Split for historical reasons
- `backend/prompts.py` (root) - Could be in `agents/` or `prompts/` directory

## Recommended Structure for Growth

If scaling this codebase:
```
backend/
├── api/           # Move routes here
│   ├── auth.py
│   ├── requirements.py
│   └── health.py
├── core/          # Config, logging
├── domain/        # Models, services
├── infrastructure/ # LLM, SSE, TaskQueue
└── agents/        # Keep as-is
```
