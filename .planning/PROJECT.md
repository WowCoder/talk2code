# Talk2Code 技术栈升级

## What This Is

Talk2Code 是一个 AI 驱动的代码生成平台，用户输入自然语言需求 → AI 多智能体协同处理 → 实时生成可运行的产品代码。当前项目需要进行技术栈升级，包括 Python 版本和 LangChain 框架。

## Core Value

保持项目技术栈现代化，利用 LangChain 最新特性和 Python 新特性，提升代码质量和可维护性。

## Requirements

### Validated

- ✓ 现有 LangGraph 工作流（研究员→产品经理→架构师→工程师）正常运行
- ✓ Flask 应用启动并提供 API 服务
- ✓ 用户认证和 SSE 实时推送功能正常

### Active

- [ ] 升级 Python 到 3.11.11（pyenv 可用最新版本）
- [ ] 升级 langchain-core 从 0.1.x 到 1.x 最新版本
- [ ] 升级 langgraph 到最新版本
- [ ] 迁移代码使用 LangChain 新 API（如 v1.x 的 ChatPromptTemplate 等）
- [ ] 确保所有现有功能在升级后正常工作
- [ ] 更新测试以覆盖新特性

### Out of Scope

- 不改变现有的 AI 智能体工作流架构
- 不引入新的外部依赖（除非必要）
- 不重写前端代码

## Context

**技术环境：**
- 当前 Python 版本：通过 pyenv 管理，目标 3.11.11
- 当前 LangChain：langchain-core>=0.1.0, langgraph>=0.0.40
- 目标 LangChain：langchain-core 1.x, langchain 1.x
- 应用类型：Flask + LangGraph + DashScope API

**代码库状态：**
- 已有代码库映射完成（`.planning/codebase/` 7 个文档）
- 核心功能：用户认证、需求管理、AI 工作流、SSE 推送
- 测试覆盖：部分单元测试，缺少集成测试

## Constraints

- **[兼容性]**: 升级后必须保持现有 API 接口不变 — 前端和外部调用方不应受影响
- **[渐进式]**: 需要逐步迁移，不能一次性破坏所有功能 — 保持可运行状态
- **[Python 版本]**: 目标版本必须在 pyenv 中可用 — 已确认 3.11.11 已安装
- **[API 密钥]**: DashScope API 需要有效配置 — 升级过程不影响现有密钥

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Python 3.11.11 作为目标版本 | pyenv 中已安装的最新版本，Python 3.11 性能提升明显 | — Pending |
| 升级到 LangChain 1.x | 最新稳定版本，支持新特性和更好的模块化 | — Pending |
| 保留 LangGraph 工作流设计 | 现有 4 智能体架构工作良好，只需 API 迁移 | — Pending |
| 使用 langchain-core 而非完整 langchain | 更轻量的依赖，只引入需要的组件 | — Pending |

## Evolution

本技术在项目阶段转换和里程碑边界处演进。

**每个阶段转换后**（通过 `/gsd-plan-phase` 和 `/gsd-execute-phase`）：
1. _requirements 已更新？_ → 移动到 Validated 并标注阶段
2. _新需求出现？_ → 添加到 Active
3. _决策需要记录？_ → 添加到 Key Decisions

---
*Last updated: 2026-04-16 after technical stack upgrade initialization*
