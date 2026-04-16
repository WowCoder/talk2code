# Requirements: Talk2Code 技术栈升级

**Defined:** 2026-04-16
**Core Value:** 保持项目技术栈现代化，利用 LangChain 最新特性和 Python 新特性

## v1 Requirements

### Python 版本升级

- [ ] **PY-01**: 创建 `.python-version` 文件指定 Python 3.11.11
- [ ] **PY-02**: 使用 pyenv 安装/确认 Python 3.11.11 已安装
- [ ] **PY-03**: 创建 Python 3.11.11 虚拟环境
- [ ] **PY-04**: 验证虚拟环境 Python 版本正确

### 依赖升级

- [ ] **DEP-01**: 更新 `requirements.txt` 指定新版本
  - langchain-core>=1.0.0
  - langgraph>=0.1.0 (或最新)
  - 保持其他依赖兼容
- [ ] **DEP-02**: 安装新依赖并解决冲突
- [ ] **DEP-03**: 验证安装成功无报错

### LangChain API 迁移

- [ ] **API-01**: 更新导入语句使用新包结构
  - `from langchain_core.messages import HumanMessage, SystemMessage`
  - `from langchain_core.prompts import ChatPromptTemplate`
- [ ] **API-02**: 迁移 `llm/client.py` 使用新 API
- [ ] **API-03**: 迁移 `agents/nodes.py` 使用新 prompt 模板
- [ ] **API-04**: 迁移 `agents/workflow.py` 使用新 StateGraph API

### 测试验证

- [ ] **TEST-01**: 运行现有单元测试确保通过
- [ ] **TEST-02**: 验证 LLM 客户端调用正常
- [ ] **TEST-03**: 验证 LangGraph 工作流执行正常
- [ ] **TEST-04**: 启动 Flask 应用验证无启动错误

### 功能验证

- [ ] **FUNC-01**: 测试用户登录/注册功能
- [ ] **FUNC-02**: 测试创建需求功能
- [ ] **FUNC-03**: 测试 AI 工作流生成代码
- [ ] **FUNC-04**: 测试 SSE 实时推送
- [ ] **FUNC-05**: 测试持续对话功能

## v2 Requirements

### 新特性采用

- 使用 LangChain 1.x 的新 prompt 缓存特性
- 采用新的异步 API (如果适用)
- 使用 langchain-core 的类型提示改进

### 测试改进

- 添加集成测试覆盖 LangGraph 工作流
- 添加 E2E 测试验证完整流程

## Out of Scope

| Feature | Reason |
|---------|--------|
| 重写 AI 智能体架构 | 现有工作流设计良好，只需 API 迁移 |
| 前端代码修改 | 升级不影响前端接口 |
| 数据库 schema 变更 | 与本次升级无关 |
| 添加新的 AI 模型集成 | 保持使用现有 DashScope API |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| PY-01 | Phase 1 | Pending |
| PY-02 | Phase 1 | Pending |
| PY-03 | Phase 1 | Pending |
| PY-04 | Phase 1 | Pending |
| DEP-01 | Phase 1 | Pending |
| DEP-02 | Phase 1 | Pending |
| DEP-03 | Phase 1 | Pending |
| API-01 | Phase 2 | Pending |
| API-02 | Phase 2 | Pending |
| API-03 | Phase 2 | Pending |
| API-04 | Phase 2 | Pending |
| TEST-01 | Phase 3 | Pending |
| TEST-02 | Phase 3 | Pending |
| TEST-03 | Phase 3 | Pending |
| TEST-04 | Phase 3 | Pending |
| FUNC-01 | Phase 3 | Pending |
| FUNC-02 | Phase 3 | Pending |
| FUNC-03 | Phase 3 | Pending |
| FUNC-04 | Phase 3 | Pending |
| FUNC-05 | Phase 3 | Pending |

**Coverage:**
- v1 requirements: 19 total
- Mapped to phases: 19
- Unmapped: 0 ✓

---
*Requirements defined: 2026-04-16*
*Last updated: 2026-04-16 after initial definition*
