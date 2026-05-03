# Talk2Code Agent 范式重构

## What This Is

Talk2Code 是一个 AI 驱动的代码生成平台，用户输入自然语言需求，AI 多智能体协同处理并实时生成可运行代码。当前项目已完成技术栈升级（Python 3.11 + LangChain 1.x），下一步核心工作是重构 AI 智能体架构，从低效的 4 节点流水线升级为 Planner+Coder 的两阶段范式。

## Core Value

用最小的 LLM 调用成本，生成高质量的完整前端代码。每个需求处理的延迟和 token 消耗减半，代码质量不降反升。

## Requirements

### Validated

- ✓ 现有 LangGraph 工作流（研究员→产品经理→架构师→工程师）正常运行
- ✓ Flask 应用启动并提供 API 服务
- ✓ 用户认证和 SSE 实时推送功能正常
- ✓ LangChain 1.x + LangGraph 升级完成

### Active

- [ ] **PLN-01**: 合并研究员、产品经理、架构师为 Planner 节点，产出结构化 Plan JSON
- [ ] **COD-01**: 改造 Coder 节点，使用结构化 Plan 替代 compress_outputs 压缩文本
- [ ] **VAL-01**: 新增 Validator 节点，静态检查 + LLM 代码审查
- [ ] **VAL-02**: 实现验证循环（不通过则回退 Coder 重试，最多 2 次）
- [ ] **WFL-01**: 重构 Workflow DAG 从 4 节点线性改为 3 节点 + 条件循环
- [ ] **SSE-01**: 更新 SSE 推送内容适配新节点（Plan 展示 + 代码展示）
- [ ] **CLN-01**: 清理旧节点（researcher/product_manager/architect）和旧 prompt
- [ ] **API-01**: Chat 修改接口支持独立触发 Coder（不重跑 Planner）

### Out of Scope

- Supervisor/Router 范式 — Talk2Code 只有两个固定场景，不需要动态路由
- 引入新的 LLM provider — 继续使用 DashScope
- 重写前端 — SSE 推送接口兼容，前端可选适配
- 大规模测试覆盖 — 保留现有 fallback 兜底即可

## Context

**架构现状：**
- 4 个串联 LLM 调用：研究员(2000tok) → 产品经理(2000tok) → 架构师(2000tok) → 工程师(4000tok)
- 前 3 个节点输出经过 `compress_outputs()` 关键词提取压缩为 ~200 字传给工程师
- 研究员的"市场需求分析"对工程师写代码完全无用
- 信息压缩率 ~95% 丢失

**技术栈：**
- LangGraph StateGraph 工作流
- LangChain ChatPromptTemplate prompts
- DashScope API (qwen 系列模型)
- SSE 实时推送

**已知问题：**
- `generate_fallback_code()` 包含完整模板代码，质量与 LLM 产出相当
- 说明简单应用不需要 4 轮 LLM 调用

## Constraints

- **[兼容性]**: 输出格式不变（code_files JSON 数组），前端无需重写
- **[兜底]**: 保留 `generate_fallback_code()` 作为 Coder 失败时的备用
- **[增量]**: 先完成核心重构，Validator 可后续增强
- **[LLM Provider]**: 继续用 DashScope，不引入新依赖

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Planner 产出结构化 JSON | Coder 需要完整信息而非压缩文本 | ✓ 信息无损传递 |
| 合并研究员+产品经理+架构师 | 研究员输出对写代码无用 | ⚠️ 待验证代码质量 |
| Validator 最多重试 2 次 | 2 次是性价比拐点 | — Pending |
| 保留 Fallback 代码 | LLM 调用失败时兜底 | ✓ 向后兼容 |

## Evolution

本技术在项目阶段转换和里程碑边界处演进。

**每个阶段转换后**（通过 `/gsd-plan-phase` 和 `/gsd-execute-phase`）：
1. _requirements 已更新？_ → 移动到 Validated 并标注阶段
2. _新需求出现？_ → 添加到 Active
3. _决策需要记录？_ → 添加到 Key Decisions

---
*Last updated: 2026-04-29 after agent paradigm refactoring initialization*
