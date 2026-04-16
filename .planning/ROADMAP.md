# Roadmap: Talk2Code 技术栈升级

**Created:** 2026-04-16
**Project:** Talk2Code Python & LangChain 升级

## Summary

| Metric | Value |
|--------|-------|
| Total Phases | 3 |
| Total Requirements | 19 |
| Estimated Timeline | 3-5 days |

---

## Phase 1: Python 版本与依赖升级

**Goal:** 完成 Python 3.11.11 环境设置和依赖包升级

**Requirements Covered:**
- PY-01, PY-02, PY-03, PY-04 (Python 版本)
- DEP-01, DEP-02, DEP-03 (依赖升级)

**Plans:** 3 plans

**Plans:**
- [x] 01-01-PLAN.md — Python 3.11.11 environment setup (.python-version, backend/.venv) `completed: 2026-04-16`
- [x] 01-02-PLAN.md — Dependency upgrade (langchain-core>=1.0.0, langgraph>=0.1.0) `completed: 2026-04-16`
- [x] 01-03-PLAN.md — Verification (imports, pytest, Flask startup) `completed: 2026-04-16`

**Status:** ✓ Complete (2026-04-16)

**Success Criteria:**
1. `.python-version` 文件创建并指定 3.11.11
2. 虚拟环境使用 Python 3.11.11
3. `pip list` 显示 langchain-core>=1.0.0
4. Flask 应用可以启动无 import 错误

**Deliverables:**
- `.python-version` 文件
- 更新的 `requirements.txt`
- 新的虚拟环境

---

## Phase 2: LangChain API 迁移

**Goal:** 迁移代码使用 LangChain 1.x 新 API

**Requirements Covered:**
- API-01, API-02, API-03, API-04

**Plans:** 3 plans

**Plans:**
- [ ] 02-01-PLAN.md — Import verification and langchain_core type integration
- [ ] 02-02-PLAN.md — Migrate prompts to ChatPromptTemplate format
- [ ] 02-03-PLAN.md — Workflow verification and integration tests

**Success Criteria:**
1. 所有导入语句使用新包结构 (`langchain_core.*`)
2. `llm/client.py` 集成 langchain_core 类型 (保持自定义 DashScope 客户端)
3. `agents/nodes.py` 使用 ChatPromptTemplate 格式化消息
4. `agents/workflow.py` StateGraph 通过验证 (已是 1.x 兼容)
5. 添加集成测试验证工作流结构

**Deliverables:**
- 更新的 `backend/llm/client.py` (添加 langchain_core 类型转换)
- 更新的 `backend/prompts.py` (ChatPromptTemplate 格式)
- 更新的 `backend/agents/nodes.py` (使用 format_messages)
- 更新的 `backend/agents/workflow.py` (类型注解)
- 新增 `backend/tests/unit/test_imports.py`
- 新增 `backend/tests/integration/test_workflow.py`

---

## Phase 3: 测试与验证

**Goal:** 验证所有功能在升级后正常工作

**Requirements Covered:**
- TEST-01, TEST-02, TEST-03, TEST-04
- FUNC-01, FUNC-02, FUNC-03, FUNC-04, FUNC-05

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

---

## Traceability Matrix

| Requirement | Phase | Status |
|-------------|-------|--------|
| PY-01 ~ PY-04 | Phase 1 | Pending |
| DEP-01 ~ DEP-03 | Phase 1 | Pending |
| API-01 ~ API-04 | Phase 2 | Pending |
| TEST-01 ~ TEST-04 | Phase 3 | Pending |
| FUNC-01 ~ FUNC-05 | Phase 3 | Pending |

**Coverage:** 100% (19/19 requirements mapped)

---
*Roadmap created: 2026-04-16*
