# Tech Stack: Talk2Code

**Analyzed:** 2026-04-16
**Project Type:** AI-driven code generation platform

## Languages & Runtime

| Language | Version | Usage |
|----------|---------|-------|
| Python | 3.8+ | Backend logic, AI agents, API |
| JavaScript | ES6+ | Frontend interactivity |
| HTML5/CSS3 | - | Frontend structure and styling |

## Backend Framework

**Flask 2.0+** - Primary web framework

Key files:
- `backend/app.py` - Main Flask application
- `backend/config.py` - Configuration management with Pydantic

## Frontend Stack

| Library | Purpose |
|---------|---------|
| Tailwind CSS | Utility-first styling |
| CodeMirror | Code editor with syntax highlighting |
| Vanilla JS | DOM manipulation, SSE handling |

Frontend files:
- `frontend/login.html` - Login/registration page
- `frontend/index.html` - Home page (requirement input)
- `frontend/detail.html` - Requirement detail page

## Database

**SQLite** via SQLAlchemy

```
backend/vcd.db (created at runtime)
```

Key files:
- `backend/models.py` - SQLAlchemy models
- `backend/models/models.py` - User model
- `backend/models/schema.py` - Schema definitions

## AI/LLM Integration

| Component | Library |
|-----------|---------|
| LLM Client | DashScope API (Aliyun Bailian) |
| Orchestration | LangGraph + LangChain |
| Models | Qwen-plus, Qwen-turbo, Qwen-max |

Key files:
- `backend/llm/client.py` - Unified LLM client
- `backend/agents/workflow.py` - LangGraph StateGraph
- `backend/agents/nodes.py` - Agent nodes (Researcher, PM, Architect, Engineer)
- `backend/prompts.py` - System prompt templates

## Authentication

**JWT (JSON Web Tokens)** via Flask-JWT-Extended

- Token-based authentication
- 24-hour token expiry (configurable)
- Protected routes via `@jwt_required()`

## Real-time Communication

**SSE (Server-Sent Events)** - Custom implementation

Key files:
- `backend/services/sse_manager.py` - Thread-safe SSE manager
- `backend/utils/sse.py` - SSE message formatting

## Dependencies (requirements.txt)

```
flask>=2.0.0
flask-jwt-extended>=4.0.0
flask-cors>=3.0.0
flask-limiter>=3.0.0
sqlalchemy>=1.4.0
bcrypt>=3.2.0
requests>=2.28.0
langchain-core>=0.1.0
langgraph>=0.0.40
pydantic>=2.0.0
pydantic-settings>=2.0.0
pytest>=7.0.0
pytest-cov>=4.0.0
pytest-mock>=3.10.0
```

## Configuration Management

**Pydantic Settings** - Type-safe configuration

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
```

Environment variables loaded from `.env` file.

## Testing

| Framework | Purpose |
|-----------|---------|
| pytest | Test runner |
| pytest-cov | Coverage reporting |
| pytest-mock | Mocking utilities |

Test files:
- `backend/tests/unit/test_llm_client.py`
- `backend/tests/unit/test_diff_utils.py`
- `backend/tests/unit/test_security.py`
- `backend/tests/unit/test_health.py`
- `backend/tests/conftest.py`

## Key Configuration Values

| Config | Default | Notes |
|--------|---------|-------|
| `DASHSCOPE_MODEL` | qwen-plus | Aliyun Bailian model |
| `JWT_ACCESS_TOKEN_EXPIRES` | 24 hours | Token validity |
| `SSE_RETRY_TIMEOUT` | 1000ms | SSE reconnection |
| `LLM_TEMPERATURE` | 0.7 | LLM creativity |
| `LLM_MAX_TOKENS` | 4000 | Max generation |
| `LLM_TIMEOUT` | 60s | API timeout |
| `LLM_MAX_RETRIES` | 2 | Retry attempts |

## Build/Run

```bash
cd backend
pip install -r requirements.txt
python app.py
```

Server runs on `http://localhost:5001`
