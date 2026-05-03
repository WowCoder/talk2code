# Roadmap: Talk2Code Agent 范式重构

**Created:** 2026-04-29
**Project:** Talk2Code Agent 范式重构（Planner + Coder）

## Summary

| Metric | Value |
|--------|-------|
| Total Phases | 3 |
| Total Requirements | 17 |
| Estimated Timeline | 2-3 days |

---

## Phase 1: Planner + State + Workflow 重构

**Goal:** 删除研究员/产品经理/架构师节点，替换为 Planner 节点；重构 AgentState；更新 Workflow DAG

**Requirements Covered:**
- PLN-01, PLN-02 (Planner 节点)
- WFL-01, WFL-02 (AgentState + Workflow DAG)

**Plans:**
- [ ] 01-01-PLAN.md — 重构 AgentState：删除 agent_outputs，新增 plan、validation_result、retry_count
- [ ] 01-02-PLAN.md — 创建 Planner 节点：合并研究员+产品经理+架构师职责，产出结构化 Plan JSON
- [ ] 01-03-PLAN.md — 重构 Workflow DAG：从 4 节点线性改为 Planner→Coder→Validator→条件循环

---

## Phase 2: Coder + Validator + SSE 适配

**Goal:** 改造 Coder 使用结构化 Plan；新增 Validator 节点；更新 SSE 推送

**Requirements Covered:**
- COD-01, COD-02, COD-03 (Coder 节点)
- VAL-01, VAL-02, VAL-03, VAL-04 (Validator 节点)
- WFL-03, SSE-01, SSE-02, SSE-03 (进度+推送)

**Plans:**
- [ ] 02-01-PLAN.md — 改造 Coder 节点：使用 Plan JSON 替代 compress_outputs，保留 fallback 逻辑
- [ ] 02-02-PLAN.md — 创建 Validator 节点：静态检查 + LLM 审查 + fix_instructions
- [ ] 02-03-PLAN.md — SSE 推送适配：更新进度映射和对话内容
- [ ] 02-04-PLAN.md — 清理旧代码：删除 researcher/product_manager/architect 节点和旧 prompt

---

## Phase 3: Chat 修改接口 + 验证

**Goal:** 对话修改场景独立触发 Coder；端到端验证

**Requirements Covered:**
- API-01, API-02 (Chat 修改接口)

**Plans:**
- [ ] 03-01-PLAN.md — 改造 chat API：区分新建需求（Planner+Coder）和对话修改（仅 Coder）
- [ ] 03-02-PLAN.md — 端到端验证：创建需求触发新 pipeline，chat 修改触发 Coder

---

## Traceability Matrix

| Requirement | Phase | Status |
|-------------|-------|--------|
| PLN-01 | Phase 1 | Pending |
| PLN-02 | Phase 1 | Pending |
| COD-01 | Phase 2 | Pending |
| COD-02 | Phase 2 | Pending |
| COD-03 | Phase 2 | Pending |
| VAL-01 | Phase 2 | Pending |
| VAL-02 | Phase 2 | Pending |
| VAL-03 | Phase 2 | Pending |
| VAL-04 | Phase 2 | Pending |
| WFL-01 | Phase 1 | Pending |
| WFL-02 | Phase 1 | Pending |
| WFL-03 | Phase 2 | Pending |
| SSE-01 | Phase 2 | Pending |
| SSE-02 | Phase 2 | Pending |
| SSE-03 | Phase 2 | Pending |
| API-01 | Phase 3 | Pending |
| API-02 | Phase 3 | Pending |

**Coverage:** 100% (17/17 requirements mapped) - All Pending

---
*Roadmap created: 2026-04-29*
