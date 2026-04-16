---
plan: 02-01
phase: 02
status: complete
executed: 2026-04-17
---

# Plan 02-01: Import verification and langchain_core type integration

**Objective:** Verify and migrate imports to langchain_core.* namespace, evaluate langchain-dashscope integration

---

## What Was Built

### 1. Import Verification Test Suite

Created `backend/tests/unit/test_imports.py` with 18 comprehensive tests:

**TestLangChainCoreImports (7 tests):**
- Verifies all langchain_core message types can be imported
- Tests HumanMessage, SystemMessage, AIMessage, BaseMessage
- Tests ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate

**TestNoDeprecatedImports (2 tests):**
- Scans all source files for deprecated `from langchain.schema` imports
- Scans for deprecated `from langchain.prebuilt` imports
- Excludes test files and virtual environments from scan

**TestProjectImportsClean (9 tests):**
- Verifies project modules import correctly
- Tests agents.state, agents.workflow, agents.nodes
- Tests llm.client and prompts modules

### 2. LLM Client Enhancements

**Added langchain_core imports to `backend/llm/client.py`:**
```python
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
```

**Added conversion function:**
```python
def to_langchain_message(msg: Message) -> BaseMessage:
    """Convert internal Message to langchain_core BaseMessage"""
    if msg.role == 'user':
        return HumanMessage(content=msg.content)
    elif msg.role == 'assistant':
        return AIMessage(content=msg.content)
    else:
        return SystemMessage(content=msg.content)
```

**Added explanatory comment:** Documented why we use direct DashScope REST API instead of langchain-dashscope wrapper (better control over retry logic, streaming, and timeout handling).

---

## Key Files Created/Modified

| File | Action | Purpose |
|------|--------|---------|
| `backend/tests/unit/test_imports.py` | Created | Import verification test suite |
| `backend/llm/client.py` | Modified | Added langchain_core types and conversion function |

---

## Test Results

```
pytest tests/unit/test_imports.py -x
18 passed in 2.23s
```

All acceptance criteria verified:
- ✓ langchain_core imports work correctly
- ✓ No deprecated `from langchain.schema` imports in codebase
- ✓ `to_langchain_message()` function exists and converts correctly
- ✓ Explanatory comment added for direct API decision

---

## Self-Check: PASSED

**Verification:**
- `pytest backend/tests/unit/test_imports.py -x` — 18 tests passed
- `grep "from langchain.schema" backend/*.py backend/**/*.py` — no results (clean)
- `grep "from langchain_core" backend/llm/client.py` — returns import statement

---

## Notes

- Research conclusion from 02-RESEARCH.md confirmed: keeping custom DashScope client is the right choice
- The `to_langchain_message()` function enables future LangChain integration without breaking existing code
- Test suite provides regression protection against accidentally reintroducing deprecated imports
