# Conventions: Talk2Code

**Analyzed:** 2026-04-16

## Code Style

### Python Style Guide

**General:**
- Follows PEP 8 conventions
- 4 spaces for indentation
- Maximum line length: Not enforced (long lines observed)
- UTF-8 encoding declared at top of files: `# -*- coding: utf-8 -*-`

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Modules | snake_case | `rate_limiter.py`, `sse_manager.py` |
| Packages | lowercase | `agents/`, `services/` |
| Functions | snake_case | `get_logger()`, `setup_logger()` |
| Variables | snake_case | `current_user_id`, `dialogue_history` |
| Classes | PascalCase | `Settings`, `SSEManager`, `BailianLLM` |
| Constants | UPPER_SNAKE_CASE | `JWT_SECRET_KEY`, `RATE_LIMITS` |
| Private | Leading underscore | `_settings` |

### Function Structure

```python
def function_name(arg1, arg2):
    """
    Docstring describing purpose.
    
    Args:
        arg1: Description
        arg2: Description
    
    Returns:
        Type: Description
    """
    # Implementation
```

**Actual usage in codebase:**
```python
# backend/config.py
def get_settings() -> Settings:
    """获取配置单例"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

## Error Handling

### Try-Except-Finally Pattern

```python
# backend/app.py - Database operations
db = SessionLocal()
try:
    user = db.query(User).filter(User.id == current_user_id).first()
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    return jsonify({'user': {...}}), 200
except Exception as e:
    logger.error(f"Error: {e}", exc_info=True)
    db.rollback()
    return jsonify({'error': f'处理失败：{str(e)}'}), 500
finally:
    db.close()
```

### Custom Exceptions

**Not extensively used.** Mainly relies on:
- Standard Python exceptions
- Flask request exceptions
- SQLAlchemy exceptions

### Logging

```python
from utils.logger import get_logger

logger = get_logger(__name__)

# Usage levels:
logger.debug("Debug message")
logger.info("Info message")
logger.warning("Warning message")
logger.error("Error message", exc_info=True)
```

**Log configuration:**
```python
# backend/config.py
LOG_LEVEL: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] = 'INFO'
LOG_FILE: str = 'logs/app.log'
```

## Documentation

### Module Docstrings

```python
# -*- coding: utf-8 -*-
"""
配置管理模块
使用 Pydantic 进行配置验证
"""
```

### Function Docstrings

**Inconsistent usage:**
- Some functions have docstrings
- Many functions have no docstrings
- Chinese language used for docstrings

### Inline Comments

```python
# ==================== Section Headers ====================
# Used to separate major code sections

# Inline comments explain specific lines
# 限流触发处理
@app.errorhandler(429)
def handle_rate_limit_exceeded(e):
    return rate_limit_handler(e)
```

## Import Organization

### Standard Order

```python
# 1. Standard library
import os
import sys
import atexit

# 2. Third-party
from flask import Flask, request, jsonify
from flask_jwt_extended import JWTManager

# 3. First-party
from config import JWT_SECRET_KEY
from models import init_db
from services.sse_manager import sse_manager

# 4. Local imports (inside functions for circular deps)
from models import User, SessionLocal
```

### Circular Dependency Handling

```python
# Use local imports inside functions
def some_function():
    from models import User, SessionLocal
    # ... use User and SessionLocal
```

## Type Hints

### Usage Pattern

**Pydantic models use type hints:**
```python
class Settings(BaseSettings):
    DATABASE_NAME: str = Field(default='vcd.db')
    JWT_ACCESS_TOKEN_EXPIRES_HOURS: int = Field(default=24)
    LLM_TEMPERATURE: float = Field(default=0.7, ge=0, le=2)
```

### Type Checking

**Not configured.** No `mypy` or similar type checker in use.

**Imports suggest awareness:**
```python
from typing import Dict, Literal, Optional, List
```

## Security Patterns

### Password Hashing

```python
# backend/utils/security.py
from passlib.hash import bcrypt

def hash_password(password: str) -> str:
    return bcrypt.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.verify(password, hashed)
```

### JWT Authentication

```python
from flask_jwt_extended import jwt_required, get_jwt_identity

@app.route('/api/protected')
@jwt_required()
def protected_route():
    current_user_id = get_jwt_identity()
    # ... use current_user_id
```

### Rate Limiting

```python
from flask_limiter import Limiter
from utils.rate_limiter import RATE_LIMITS

@limiter.limit(RATE_LIMITS['auth'])
@app.route('/api/login', methods=['POST'])
def login():
    # ...
```

## Flask Patterns

### Blueprint Usage

**Not used.** All routes in single `app.py`.

### Application Factory

**Not used.** Single monolithic app initialization.

### Configuration

**Pydantic Settings:**
```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore'
    )
```

## Frontend Conventions

### HTML Structure

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Page Title</title>
    <!-- Tailwind CSS via CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- CodeMirror for code editor -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/CodeMirror/5.x/codemirror.min.css">
</head>
<body>
    <!-- Content -->
</body>
</html>
```

### JavaScript Patterns

```javascript
// Vanilla JS, no framework
async function fetchData(url, options) {
    const response = await fetch(url, options);
    return await response.json();
}

// Event listeners
document.addEventListener('DOMContentLoaded', () => {
    // Initialize
});
```

### SSE Handling

```javascript
const eventSource = new EventSource(`/api/sse/${requirementId}`);

eventSource.addEventListener('connected', (event) => {
    const data = JSON.parse(event.data);
    console.log('Connected:', data);
});

eventSource.addEventListener('message', (event) => {
    const data = JSON.parse(event.data);
    // Handle message
});
```

## Database Conventions

### SQLAlchemy Patterns

```python
from sqlalchemy import Column, Integer, String, DateTime, Text, JSON, ForeignKey
from sqlalchemy.orm import relationship

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    create_time = Column(DateTime, default=datetime.utcnow)
```

### Session Management

```python
db = SessionLocal()
try:
    # ... database operations
    db.commit()
except Exception:
    db.rollback()
    raise
finally:
    db.close()
```

## Git Conventions

### Commit Messages

**Not standardized.** No `CONTRIBUTING.md` or commit message template found.

### Branch Naming

**Not documented.**

## Code Review Checklist

Based on observed patterns:

- [ ] UTF-8 encoding declared
- [ ] Function has docstring (Chinese)
- [ ] Error handling with try-except-finally
- [ ] Database session properly closed
- [ ] Logging at appropriate level
- [ ] Type hints for new code
- [ ] No hardcoded values (use config)
- [ ] Security considerations (SQL injection, XSS)
