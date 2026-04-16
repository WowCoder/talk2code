# Integrations: Talk2Code

**Analyzed:** 2026-04-16

## External APIs

### 1. Aliyun DashScope API (百炼)

**Purpose:** LLM inference for AI agents

| Property | Value |
|----------|-------|
| Provider | Aliyun Bailian |
| Models | qwen-plus, qwen-turbo, qwen-max, qwen-max-longcontext |
| Base URL | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Auth | API Key via header |

**Configuration:**
```python
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY')
DASHSCOPE_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
```

**Usage locations:**
- `backend/llm/client.py` - HTTP client for API calls
- `backend/agents/nodes.py` - Agent nodes call LLM
- `backend/app.py` - Chat endpoint uses LLM client

**Retry logic:**
- Max 2 retries
- Exponential backoff
- 60s timeout per request

## Databases

### SQLite

**Purpose:** Primary data storage

| Property | Value |
|----------|-------|
| File | `backend/vcd.db` |
| Driver | SQLAlchemy sqlite3 |
| URI | `sqlite:///backend/vcd.db` |

**Tables:**
1. `users` - User accounts
2. `requirements` - User requirements and generated code

## Authentication Providers

### JWT (Internal)

**Not integrated with external auth providers.**

Current auth is self-contained:
- Username/password stored in local database
- Passwords hashed with bcrypt
- JWT tokens for session management

**No OAuth integration** (Google, GitHub, etc. not implemented)

## Webhooks

**None configured.**

The application does not:
- Receive webhooks from external services
- Send webhooks to external services

## Third-Party Services

### CORS

**flask-cors** - Cross-Origin Resource Sharing

Allows frontend to make requests to backend from different origins.

### Rate Limiting

**flask-limiter** - Request rate limiting

Storage: In-memory (not persistent)

Rate limits configured in `backend/utils/rate_limiter.py`:
- Auth endpoints: Limited requests per minute
- Requirement creation: Limited requests per minute
- Chat: Limited requests per minute
- Default: Global fallback limit

## File System

### Static Files

```
frontend/
├── login.html
├── index.html
└── detail.html
```

Served directly by Flask via `send_from_directory()`.

### Logs

```
logs/app.log
```

Configurable via `LOG_FILE` setting.

## No External Integrations For:

| Service | Status |
|---------|--------|
| Email (password reset) | Not implemented |
| SMS | Not implemented |
| Cloud storage | Not implemented |
| CI/CD | Not implemented |
| Monitoring (Sentry, etc.) | Not implemented |
| Analytics | Not implemented |

## Security Considerations

1. **API Key Storage:** Should use environment variables, not hardcoded
2. **JWT Secret:** Default value should be changed in production
3. **Database:** SQLite is file-based, not suitable for production scale
4. **Rate Limiting:** In-memory storage resets on restart

## Potential Future Integrations

Based on codebase structure:
- Email service (SendGrid, SES) for password reset
- Object storage (S3, OSS) for code file storage
- PostgreSQL/MySQL for production database
- Redis for rate limiting persistence
- Kubernetes health probes (already implemented: `/api/health/live`, `/api/health/ready`)
