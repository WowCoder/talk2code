---
phase: 1
slug: python-langchain-upgrade
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-16
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x+ |
| **Config file** | `backend/pytest.ini` (需验证是否存在) |
| **Quick run command** | `pytest backend/tests/unit/ -x` |
| **Full suite command** | `pytest backend/tests/ -v` |
| **Estimated runtime** | ~60 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest backend/tests/unit/ -x`
- **After every plan wave:** Run `pytest backend/tests/ -v`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~30 seconds (unit tests)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | PY-01 | — | `.python-version` contains `3.11.11` | manual | `cat .python-version` | ❌ W1 | ⬜ pending |
| 01-01-02 | 01 | 1 | PY-02 | — | Python 3.11.11 installed | manual | `pyenv versions \| grep 3.11.11` | ✅ | ⬜ pending |
| 01-01-03 | 01 | 1 | PY-03 | — | `.venv/bin/python` exists | manual | `ls -la .venv/bin/python` | ❌ W1 | ⬜ pending |
| 01-02-01 | 02 | 2 | DEP-01 | — | `requirements.txt` contains `langchain-core>=0.3.0` | manual | `grep langchain-core backend/requirements.txt` | ✅ | ⬜ pending |
| 01-02-02 | 02 | 2 | DEP-02 | — | pip install exits 0 | automated | `pip install -r backend/requirements.txt` | ✅ | ⬜ pending |
| 01-02-03 | 02 | 2 | DEP-03 | — | Imports succeed | automated | `python -c "import langchain_core; import langgraph"` | ✅ | ⬜ pending |
| 01-03-01 | 03 | 3 | PY-04 | — | Virtualenv Python is 3.11.11 | manual | `source .venv/bin/activate && python --version` | ❌ W1 | ⬜ pending |
| 01-03-02 | 03 | 3 | DEP-03 | — | All imports succeed | automated | `python -c "from langchain_core.messages import HumanMessage; from langgraph.graph import StateGraph"` | ✅ | ⬜ pending |
| 01-03-03 | 03 | 3 | TEST-01 | — | Unit tests pass | automated | `pytest backend/tests/unit/ -x` | ❓ | ⬜ pending |
| 01-03-04 | 03 | 3 | TEST-04 | — | Flask starts without errors | manual | `cd backend && python app.py` (check no traceback) | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

现有测试基础设施已存在，无需 Wave 0 设置：
- pytest 已安装
- 测试文件位于 `backend/tests/`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Python version file creation | PY-01 | File creation, no test needed | `echo "3.11.11" > .python-version` then verify with `cat` |
| Virtual environment creation | PY-03 | Environment setup is one-time | `python -m venv .venv` then verify `ls .venv/bin/python` |
| Flask app startup | TEST-04 | Interactive verification | `cd backend && source .venv/bin/activate && python app.py`, check no import errors |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
