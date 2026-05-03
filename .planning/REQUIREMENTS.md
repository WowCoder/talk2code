# Requirements: Talk2Code Agent 范式重构

**Defined:** 2026-04-29
**Project:** Talk2Code Agent 范式重构（Planner + Coder）

## v1 Requirements

### Planner 节点

- [ ] **PLN-01**: Planner 合并研究员、产品经理、架构师职责，产出结构化 Plan JSON（features、tech_stack、data_model、file_structure、implementation_notes）
- [ ] **PLN-02**: Planner Prompt 支持通过 ChatPromptTemplate 格式化，传入用户需求

### Coder 节点

- [ ] **COD-01**: Coder 使用结构化 Plan JSON 作为输入上下文（替代 compress_outputs 压缩文本）
- [ ] **COD-02**: Coder 输出格式不变（code_files JSON 数组，包含 filename + content）
- [ ] **COD-03**: Coder 失败时回退到 generate_fallback_code 兜底

### Validator 节点

- [ ] **VAL-01**: Validator 先执行静态检查（文件完整性、基本 HTML 结构、跨文件引用、空文件检测）
- [ ] **VAL-02**: Validator 静态检查通过后调用 LLM 进行功能覆盖审查
- [ ] **VAL-03**: Validator 输出 pass/fail + issues + fix_instructions 结构化结果
- [ ] **VAL-04**: 验证失败时 Coder 带 fix_instructions 重新生成，最多重试 2 次

### Workflow

- [ ] **WFL-01**: AgentState 重构：删除 agent_outputs，新增 plan、validation_result、retry_count
- [ ] **WFL-02**: Workflow DAG 从 4 节点线性改为 Planner→Coder→Validator→(条件)→Coder/END
- [ ] **WFL-03**: 进度映射更新（Planner 30% → Coder 60% → Validator 80% → 完成 100%）

### SSE 推送

- [ ] **SSE-01**: Planner 完成后推送 Plan 摘要对话（功能清单要点）
- [ ] **SSE-02**: Coder 完成后推送代码文件（现有逻辑不变）
- [ ] **SSE-03**: Validator 失败时推送警告信息

### Chat 修改接口

- [ ] **API-01**: 对话修改场景下只触发 Coder 节点（不重跑 Planner）
- [ ] **API-02**: Coder 接收当前代码 + 用户修改意图，输出 diff 格式

## v2 Requirements

### Supervisor 路由

- **SUP-01**: Supervisor 路由用户意图（生成/修改/询问），动态选择 pipeline
- **SUP-02**: 不同意图触发不同 sub-agent 组合

### 前端适配

- **UI-01**: 前端新增 Plan 展示面板（功能清单、架构说明）
- **UI-02**: 前端支持 Plan 审核后再触发 Coder

## Out of Scope

| Feature | Reason |
|---------|--------|
| Supervisor/Router 范式 | Talk2Code 只有两个固定场景（生成/修改），if-else 足够 |
| 新 LLM Provider | 继续用 DashScope，无需抽象 |
| 前端重写 | SSE 接口兼容，前端可选渐进适配 |
| 大规模集成测试 | 保留 fallback 兜底，降低失败风险 |
| ReAct/Reflection 范式 | 当前场景不需要工具调用循环 |

## Traceability

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

**Coverage:**
- v1 requirements: 17 total
- Mapped to phases: 17
- Unmapped: 0

---
*Requirements defined: 2026-04-29*
