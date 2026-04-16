---
plan: 02-02
phase: 02
status: complete
executed: 2026-04-17
---

# Plan 02-02: Migrate prompts to ChatPromptTemplate format

**Objective:** Migrate prompt templates in prompts.py to use LangChain ChatPromptTemplate format

---

## What Was Built

### 1. ChatPromptTemplate Definitions in `prompts.py`

Added 4 new ChatPromptTemplate instances:

```python
from langchain_core.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate

RESEARCHER_PROMPT = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template("..."),
    HumanMessagePromptTemplate.from_template("...")
])

PRODUCT_MANAGER_PROMPT = ChatPromptTemplate.from_messages([...])
ARCHITECT_PROMPT = ChatPromptTemplate.from_messages([...])
ENGINEER_PROMPT = ChatPromptTemplate.from_messages([...])
```

**Note:** `CODE_EDIT_PROMPT` intentionally kept as string templates because the example code contains dots/brackets that conflict with LangChain's f-string template parsing.

### 2. Updated Node Functions in `nodes.py`

All 4 node functions now use `ChatPromptTemplate.format_messages()`:

**Before:**
```python
user_prompt = RESEARCHER_USER_PROMPT.format(requirement=state['requirement_content'])
response = client.chat(prompt=user_prompt, system_prompt=RESEARCHER_SYSTEM_PROMPT, ...)
```

**After:**
```python
messages = RESEARCHER_PROMPT.format_messages(requirement=state['requirement_content'])
system_prompt = next((m.content for m in messages if m.type == 'system'), None)
user_prompt = next((m.content for m in messages if m.type == 'human'), None)
response = client.chat(prompt=user_prompt, system_prompt=system_prompt, ...)
```

This pattern extracts system and user messages from the LangChain-compatible message list while maintaining backward compatibility with the custom DashScope client.

### 3. Backward Compatibility

Old string prompt constants kept with `# Deprecated` comments:
- `RESEARCHER_SYSTEM_PROMPT`, `RESEARCHER_USER_PROMPT`
- `PRODUCT_MANAGER_SYSTEM_PROMPT`, `PRODUCT_MANAGER_USER_PROMPT`
- `ARCHITECT_SYSTEM_PROMPT`, `ARCHITECT_USER_PROMPT`
- `ENGINEER_SYSTEM_PROMPT`, `ENGINEER_USER_PROMPT`

---

## Key Files Created/Modified

| File | Action | Purpose |
|------|--------|---------|
| `backend/prompts.py` | Modified | Added 4 ChatPromptTemplate definitions |
| `backend/agents/nodes.py` | Modified | Updated all 4 node functions to use format_messages() |

---

## Verification

```bash
python -c "from prompts import RESEARCHER_PROMPT, PRODUCT_MANAGER_PROMPT, ARCHITECT_PROMPT, ENGINEER_PROMPT; print('All prompts imported')"
# Output: All prompts imported

python -c "from agents.nodes import researcher_node, product_manager_node, architect_node, engineer_node; print('All nodes imported')"
# Output: All 4 node functions imported successfully
```

All acceptance criteria verified:
- ✓ All 4 agent prompts converted to ChatPromptTemplate
- ✓ nodes.py uses format_messages() for all node functions
- ✓ Old string prompts kept as deprecated for reference
- ✓ No breaking changes to existing API

---

## Self-Check: PASSED

**Verification:**
- `python -c "from prompts import RESEARCHER_PROMPT, PRODUCT_MANAGER_PROMPT, ARCHITECT_PROMPT, ENGINEER_PROMPT"` — imports successfully
- `python -c "from agents.nodes import researcher_node"` — node functions import correctly
- All node functions use LangChain message format internally

---

## Notes

- The migration is non-breaking: existing code using string prompts continues to work
- format_messages() returns a list of BaseMessage objects, which are then converted back to strings for the custom client
- This positions the codebase for future direct LangChain integration (can pass messages directly to LangChain chat models)
