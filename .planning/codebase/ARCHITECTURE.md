# Architecture: Talk2Code

**Analyzed:** 2026-04-16

## Architectural Pattern

**Layered Architecture** with modular Flask application

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (HTML/JS)                    │
│  login.html │ index.html │ detail.html                  │
└─────────────────────────────────────────────────────────┘
                          │
                          │ HTTP/REST + SSE
                          ▼
┌─────────────────────────────────────────────────────────┐
│                   Flask Application                      │
│  Routes │ Middleware │ JWT │ CORS │ Rate Limiting       │
└─────────────────────────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  Services   │  │   Agents    │  │   Models    │
│  SSE, Task  │  │  LangGraph  │  │  SQLAlchemy │
└─────────────┘  └─────────────┘  └─────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│              External APIs (DashScope LLM)               │
└─────────────────────────────────────────────────────────┘
```

## Backend Layers

### 1. Routes Layer (`backend/app.py`)

**Responsibilities:**
- HTTP request handling
- JWT authentication
- Rate limiting
- Request validation
- Response formatting

**Key routes:**
| Route | Method | Purpose |
|-------|--------|---------|
| `/api/login` | POST | User login |
| `/api/register` | POST | User registration |
| `/api/requirements` | POST/GET | Create/list requirements |
| `/api/requirements/<id>` | GET | Get requirement details |
| `/api/requirements/<id>/chat` | POST | Chat with requirement |
| `/api/sse/<id>` | GET | SSE streaming |
| `/api/health` | GET | Health check |

### 2. Services Layer (`backend/services/`)

**SSE Manager** (`sse_manager.py`):
- Thread-safe client management
- Message broadcasting
- Connection lifecycle

**Task Queue** (`task_queue.py`):
- Background job processing
- Thread pool execution
- Requirement async processing

**Requirement Service** (`requirement_service.py`):
- LangGraph workflow integration
- Agent orchestration
- Result persistence

### 3. Agents Layer (`backend/agents/`)

**LangGraph StateGraph Implementation:**

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Researcher │────▶│Product Mgr  │────▶│  Architect  │────▶│  Engineer   │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

**State Definition** (`state.py`):
```python
class AgentState(TypedDict):
    requirement_id: int
    requirement_content: str
    agent_outputs: List[dict]
    current_step: str
    code_files: Optional[List]
    error: Optional[str]
    dialogue_history: List[dict]
    metadata: dict
```

**Nodes** (`nodes.py`):
- `researcher_node()` - Market analysis
- `product_manager_node()` - Feature planning
- `architect_node()` - Technical design
- `engineer_node()` - Code generation

**Workflow** (`workflow.py`):
- StateGraph construction
- Edge definitions
- Conditional routing

### 4. Models Layer (`backend/models/`)

**SQLAlchemy ORM:**

```
users               requirements
┌─────────────┐    ┌───────────────────┐
│ id (PK)     │    │ id (PK)           │
│ username    │    │ user_id (FK)      │
│ password_hash│   │ title             │
│ create_time │    │ content           │
└─────────────┘    │ status            │
                   │ dialogue_history  │
                   │ code_files        │
                   │ create_time       │
                   │ update_time       │
                   └───────────────────┘
```

## Data Flow

### 1. User Creates Requirement

```
User → POST /api/requirements → JWT验证 → Create DB record → Submit to Task Queue
                                            │
                                            ▼
                              LangGraph Workflow (async)
                              Researcher → PM → Architect → Engineer
                                            │
                                            ▼
                              SSE Broadcast → Frontend updates
```

### 2. User Chats with Requirement

```
User → POST /api/requirements/<id>/chat → LLM call → Diff parsing → Apply changes
                                              │
                                              ▼
                                      Validate diff → Update code_files
```

### 3. SSE Streaming

```
Frontend → GET /api/sse/<id> → Queue subscription → Yield messages
                                    │
                                    ▼
                         SSE Manager broadcasts
```

## Entry Points

### Main Application
**File:** `backend/app.py`

```python
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
```

### Application Factory Pattern
Not used - single monolithic app.py

## Abstractions

### LLM Client Abstraction

`backend/llm/client.py`:
```python
class BailianLLM:
    def chat(prompt, system_prompt, **kwargs) -> Response
```

Allows swapping LLM providers without changing agent code.

### SSE Message Abstraction

`backend/utils/sse.py`:
```python
class SSEMessage:
    @staticmethod
    def format_event(event_type, data) -> str
```

Consistent SSE message formatting.

### Configuration Abstraction

`backend/config.py` with Pydantic:
```python
class Settings(BaseSettings):
    # Type-safe configuration
```

## Build Order / Dependencies

```
1. Models (database schema)
2. Config (environment loading)
3. Utils (helpers, logging)
4. Services (SSE, Task Queue)
5. Agents (LangGraph workflow)
6. Routes (Flask app)
7. Main entry point
```

## Module Boundaries

| Module | Dependencies | Dependents |
|--------|--------------|------------|
| `models/` | SQLAlchemy | `app.py`, `services/` |
| `config.py` | Pydantic | All modules |
| `utils/` | Standard lib | All modules |
| `services/` | `utils/`, `agents/` | `app.py` |
| `agents/` | `llm/`, `prompts/` | `services/` |
| `llm/` | `requests`, `config` | `agents/` |

## Kubernetes Integration

**Health probes implemented:**

- `/api/health` - Full health check
- `/api/health/live` - Liveness probe
- `/api/health/ready` - Readiness probe

## Error Handling Strategy

1. **Node-level fallback:** Each agent node has independent error handling
2. **Architect failure:** Engineer uses degraded mode if architect fails
3. **JSON parsing:** Template code generated on parse failure
4. **Database:** Rollback on transaction failure
5. **LLM:** Retry with exponential backoff
