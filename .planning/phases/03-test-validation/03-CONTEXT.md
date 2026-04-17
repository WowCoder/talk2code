# Phase 3: 测试与验证 - Context

**Gathered:** 2026-04-17
**Status:** Ready for planning
**Source:** ROADMAP.md + REQUIREMENTS.md extraction

<domain>
## Phase Boundary

This phase validates that all functionality works correctly after the Python 3.11.11 and LangChain 1.x upgrades completed in Phase 1 and Phase 2.

**Scope:**
- Run existing unit tests and verify all pass
- Validate LLM client integration with langchain_core types
- Verify LangGraph workflow execution
- Test Flask application startup
- End-to-end functional validation (login, create requirement, AI workflow, SSE, conversation)

**Out of Scope:**
- New feature development
- Database schema changes
- Frontend code modifications
- New AI model integrations

</domain>

<decisions>
## Implementation Decisions

### Test Requirements (from REQUIREMENTS.md)

**TEST-01**: Run existing unit tests to ensure they pass
- Command: `pytest` or `python -m pytest`
- Expected: All tests pass with Python 3.11.11 environment

**TEST-02**: Verify LLM client integration
- Check `backend/llm/client.py` uses langchain_core types
- Verify DashScope API calls work correctly

**TEST-03**: Verify LangGraph workflow execution
- Check `backend/agents/workflow.py` and `backend/agents/nodes.py`
- StateGraph validation passes
- Agent nodes execute correctly

**TEST-04**: Flask application startup verification
- `python backend/app.py` starts without import errors
- All routes are registered

### Functional Requirements (from REQUIREMENTS.md)

**FUNC-01**: User login/registration
- Test login with test account (test / 123456)
- Test new user registration

**FUNC-02**: Create requirement
- Submit new requirement via form
- Requirement saved to database

**FUNC-03**: AI workflow code generation
- 4-agent simulation executes (Researcher → PM → Architect → Engineer)
- Code generated based on application type

**FUNC-04**: SSE real-time push
- Events stream to browser
- Frontend receives and displays updates

**FUNC-05**: Continuous conversation
- Multi-turn dialogue works
- ConversationBufferMemory persists context

### Claude's Discretion

- Test organization structure (existing `backend/tests/` or new)
- Whether to add new integration tests vs. running existing tests only
- Manual testing vs. automated functional tests
- Specific test data/fixtures to use

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase Context
- `.planning/ROADMAP.md` — Phase 3 goal and success criteria
- `.planning/REQUIREMENTS.md` — TEST-01~04, FUNC-01~05 requirements
- `.planning/STATE.md` — Project state and recent work
- `.planning/phases/01-*/` — Phase 1 deliverables (Python 3.11.11 setup)
- `.planning/phases/02-*/` — Phase 2 deliverables (LangChain API migration)

### Codebase References
- `backend/app.py` — Flask main application, AI agent logic, SSE routes
- `backend/llm/client.py` — Custom DashScope LLM client
- `backend/agents/nodes.py` — Agent node implementations
- `backend/agents/workflow.py` — LangGraph StateGraph definition
- `backend/tests/` — Existing test suite
- `backend/requirements.txt` — Python dependencies

### Codebase Patterns
- `.planning/codebase/STACK.md` — Tech stack documentation
- `.planning/codebase/ARCHITECTURE.md` — System architecture
- `.planning/codebase/TESTING.md` — Testing patterns

</canonical_refs>

<specifics>
## Specific Ideas

### Validation Checklist from ROADMAP.md

**Success Criteria:**
1. 所有单元测试通过 (`pytest`)
2. Flask 应用启动正常
3. 用户可以登录/注册
4. 创建需求触发 AI 工作流并生成代码
5. SSE 推送正常工作
6. 持续对话功能正常

**Deliverables:**
- 测试通过报告
- 功能验证清单
- 升级完成文档

### Phase Dependencies

Phase 3 depends on:
- Phase 1: Python 3.11.11 environment (✓ Complete)
- Phase 2: LangChain 1.x API migration (✓ Complete)

</specifics>

<deferred>
## Deferred Ideas

None — all requirements in scope for this validation phase.

</deferred>

---

*Phase: 03-test-validation*
*Context gathered: 2026-04-17 via ROADMAP/REQUIREMENTS extraction*
