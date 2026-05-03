# State: Talk2Code Agent 范式重构

**Project:** Talk2Code
**Milestone:** v1.2 - Agent Paradigm Refactoring (Planner + Coder)
**Current Phase:** Phase 1 - In Progress (Planner + State + Workflow)

**Last Activity:** Phase 1 code edits completed (2026-04-29)

---

## Project Context

**Vision:** 从 4 节点流水线重构为 Planner+Coder 两阶段范式，降低 LLM 调用成本和延迟。

**Core Value:** 用最小的 LLM 调用成本，生成高质量的完整前端代码。

---

## Current Position

**Phase:** Phase 1 (Planner + State + Workflow) — Code edits complete

**Next Step:** Phase 1 verification (Flask startup test) → Phase 2 (Coder + Validator + SSE)

---

## Session Continuity

### Current Context
- 代码库已映射完成 (`.planning/codebase/` 7 个文档)
- 技术栈升级完成（Python 3.11.11 + LangChain 1.x）
- 新项目已规划（PROJECT.md, REQUIREMENTS.md, ROADMAP.md）
- Phase 1-3 (技术栈升级) 全部完成
- Phase 1 (Agent 重构) 代码编辑完成

### Completed Edits (Phase 1 - Agent Refactor)
| File | Change | Status |
|------|--------|--------|
| `agents/state.py` | AgentState 重构：删除 agent_outputs，新增 plan/validation_result/retry_count | ✓ |
| `agents/nodes.py` | 删除 researcher/product_manager/architect/compress_outputs/create_agent_output，新增 planner_node | ✓ |
| `agents/__init__.py` | 导出更新：planner_node, engineer_node | ✓ |
| `agents/workflow.py` | DAG 重构：researcher→pm→architect→engineer → planner→coder→END | ✓ |
| `prompts.py` | 删除旧 4 套 prompt + AGENT_ORDER/NAMES/PROMPTS + FALLBACK_RESPONSES，新增 PLANNER_PROMPT | ✓ |
| `services/requirement_service.py` | 进度映射/名称映射/节点检查更新 | ✓ |

### Pending (Phase 1 - Agent Refactor)
| Item | Status |
|------|--------|
| Flask 启动验证（python app.py） | ⏳ Pending |
| 测试文件更新（tests/unit/test_imports.py, tests/integration/test_workflow.py） | ⏳ Out of scope for Phase 1 |
| git commit | ⏳ Pending |

### Previous Work (Tech Stack Upgrade)
| File | Status |
|------|--------|
| Phase 1 (Python/LangChain upgrade) | ✓ Complete |
| Phase 2 (LangChain API migration) | ✓ Complete |
| Phase 3 (Testing & validation) | ✓ Complete |

### New Project Files
| File | Status |
|------|--------|
| `.planning/PROJECT.md` | Updated for agent refactor |
| `.planning/REQUIREMENTS.md` | 17 v1 requirements defined |
| `.planning/ROADMAP.md` | 3 phases planned |

---

## Project Memory

### Decisions Made (Tech Stack Upgrade - Previous Milestone)
1. Python 3.11.11 作为目标版本
2. 升级到 LangChain 1.x + LangGraph
3. 使用 `langchain-core` 包
4. 保留现有 LangGraph 工作流设计（当时）

### Decisions Made (Agent Refactor - Current Milestone)
1. Planner 产出结构化 JSON（信息无损传递）
2. 合并研究员+产品经理+架构师为单一 Planner
3. Validator 最多重试 2 次
4. 保留 Fallback 代码兜底
5. 不使用 Supervisor/Router 范式

### Open Questions
- Validator 的 LLM 审查是否值得额外 token 成本（可选启用）
- 前端是否需要 Plan 展示面板（v2 功能）

### Environment Notes
- pyenv 已安装 Python 3.11.11（但有 Apple Silicon CPU 兼容性问题，无法运行）
- LangChain 1.x + LangGraph 已安装
- DashScope API 配置有效
- `/usr/bin/python3` (3.9) 可运行但 pydantic_core 是 x86_64 架构不兼容
- Flask 启动验证暂时无法执行

---

## Todo Count: 0

---
*Last updated: 2026-04-29 after Phase 1 code edits completed*
