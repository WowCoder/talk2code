# Testing: Talk2Code

**Analyzed:** 2026-04-16

## Test Framework

| Tool | Version | Purpose |
|------|---------|---------|
| pytest | 7.0.0+ | Test runner |
| pytest-cov | 4.0.0+ | Coverage reporting |
| pytest-mock | 3.10.0+ | Mocking utilities |

## Test Structure

```
backend/tests/
├── __init__.py
├── conftest.py              # Shared fixtures
├── unit/
│   ├── __init__.py
│   ├── test_llm_client.py   # LLM client tests
│   ├── test_diff_utils.py   # Diff parsing tests
│   ├── test_security.py     # Security utility tests
│   └── test_health.py       # Health endpoint tests
└── integration/
    └── __init__.py          # (empty - no integration tests yet)
```

## Test Coverage

### Unit Tests (Implemented)

| Module | Test File | Coverage |
|--------|-----------|----------|
| `llm/client.py` | `test_llm_client.py` | ✓ |
| `diff_utils.py` | `test_diff_utils.py` | ✓ |
| `utils/security.py` | `test_security.py` | ✓ |
| `app.py` (health) | `test_health.py` | ✓ |

### Integration Tests (Not Implemented)

`tests/integration/` directory exists but is empty.

**Missing integration tests for:**
- Full requirement creation flow
- LangGraph workflow execution
- SSE streaming end-to-end
- Database transactions
- JWT authentication flow

## Test Patterns

### Fixtures (conftest.py)

```python
# Expected fixtures based on test imports:
- app: Flask test client
- client: Authenticated HTTP client
- db_session: Database session per test
```

### Mocking Strategy

**pytest-mock** for mocking:
- External API calls (DashScope)
- File system operations
- Time-dependent functions

Example pattern:
```python
def test_something(mocker):
    mock_response = mocker.patch('llm.client.requests.post')
    mock_response.return_value = {'content': 'mocked'}
```

## Testing Utilities

### Test Client Setup
```python
from flask import Flask
from app import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client
```

### Database Isolation
```python
# In-memory or temp file database per test
# Rolled back after each test
```

## How to Run Tests

```bash
cd backend

# Run all tests
pytest

# Run with coverage
pytest --cov=backend --cov-report=html

# Run specific test file
pytest tests/unit/test_llm_client.py

# Run specific test function
pytest tests/unit/test_llm_client.py::test_chat_success
```

## Test Quality Observations

### Strengths
1. ✓ Unit tests for critical utilities
2. ✓ Health endpoint testing
3. ✓ pytest-mock for external dependencies

### Gaps
1. ✗ No integration tests for main workflows
2. ✗ No E2E tests for frontend
3. ✗ Limited API route testing
4. ✗ No LangGraph workflow tests
5. ✗ No SSE streaming tests

## Recommended Test Additions

### Priority 1: API Routes
```python
# tests/unit/test_auth.py
def test_login_success
def test_login_invalid_credentials
def test_register_new_user
def test_register_duplicate_user

# tests/unit/test_requirements.py
def test_create_requirement
def test_list_requirements
def test_get_requirement_details
```

### Priority 2: Services
```python
# tests/unit/test_sse_manager.py
def test_add_client
def test_broadcast
def test_remove_client

# tests/unit/test_task_queue.py
def test_submit_task
def test_cancel_task
```

### Priority 3: Agents
```python
# tests/unit/test_workflow.py
def test_researcher_node
def test_product_manager_node
def test_architect_node
def test_engineer_node
```

### Priority 4: Integration
```python
# tests/integration/test_requirement_flow.py
def test_full_requirement_creation
def test_chat_with_code_edit
```

## CI/CD Integration

**Not configured.** No `.github/workflows/` or similar CI configuration found.

**Recommended CI setup:**
```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: "3.10"
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests
        run: pytest --cov=backend
```

## Code Coverage Goals

**Current coverage:** Unknown (not reported)

**Recommended targets:**
| Component | Target |
|-----------|--------|
| Utils | 90%+ |
| Services | 80%+ |
| Agents | 70%+ |
| Routes | 80%+ |
| Overall | 75%+ |

## Testing Conventions

### Test Naming
```python
def test_<function>_<scenario>_<expected_result>():
    # Example:
    def test_login_valid_credentials_returns_token():
    def test_create_requirement_empty_content_rejected():
```

### Arrange-Act-Assert Pattern
```python
def test_example():
    # Arrange
    data = {'username': 'test', 'password': '123456'}
    
    # Act
    response = client.post('/api/login', json=data)
    
    # Assert
    assert response.status_code == 200
    assert 'token' in response.json
```

### Mocking External Services
```python
# Always mock LLM API calls
@pytest.fixture
def mock_llm(mocker):
    return mocker.patch('llm.client.BailianLLM.chat')
```

## Test Data

**No fixtures or factories found** for creating test data.

**Recommended addition:**
```python
# tests/conftest.py
@pytest.fixture
def user(db):
    user = User(username='testuser', password_hash=hash_password('password'))
    db.add(user)
    db.commit()
    return user

@pytest.fixture
def requirement(user, db):
    req = Requirement(user_id=user.id, content='Test requirement')
    db.add(req)
    db.commit()
    return req
```
