# Phase 1: Python 版本与依赖升级 - Context

**Gathered:** 2026-04-16
**Status:** Ready for planning

<domain>
## Phase Boundary

完成 Python 3.11.11 环境设置和依赖包升级。包括：
- 创建 `.python-version` 文件指定 Python 3.11.11
- 创建虚拟环境
- 更新 `requirements.txt` 指定新版本 (langchain-core>=1.0.0, langgraph>=0.1.0)
- 安装新依赖并验证无冲突
- Flask 应用可以启动无 import 错误

</domain>

<decisions>
## Implementation Decisions

### 虚拟环境策略
- **D-01:** 使用 `.venv` 作为虚拟环境目录名（Python 标准，与现有项目结构一致）
- **D-02:** 使用 Python 内置 `venv` 模块（非 conda），命令：`python -m venv .venv`

### 依赖升级策略
- **D-03:** 采用渐进式升级策略
  1. 第一步：升级 Python 到 3.11.11，验证现有依赖可安装
  2. 第二步：升级 langchain-core 到 1.x，验证 LLM 客户端正常
  3. 第三步：升级 langgraph 到最新版，验证工作流正常
  4. 第四步：更新其他依赖到兼容版本
- **D-04:** 每次升级后运行冒烟测试，确认无问题再继续下一步

### 兼容性测试范围
- **D-05:** 运行现有单元测试 (`pytest backend/tests/`)
- **D-06:** 手动验证核心功能冒烟测试：
  - Flask 应用启动成功
  - 用户登录/注册接口可用
  - LLM 客户端调用正常
  - LangGraph 工作流可执行

### 回滚方案
- **D-07:** 升级前使用 git 保存当前状态
- **D-08:** 保留当前 `requirements.txt` 为 `requirements.txt.backup`
- **D-09:** 如需回滚，使用 git checkout 或恢复 backup 文件

### Claude's Discretion
- 具体 pip 安装命令和参数
- 虚拟环境激活脚本细节
- 测试输出的格式

</decisions>

<canonical_refs>
## Canonical References

### Python 版本管理
- `.python-version` — pyenv 使用的 Python 版本文件（将创建）

### 依赖配置
- `backend/requirements.txt` — 当前依赖列表（待更新）
- `backend/config.py` — 配置模块，验证导入是否兼容

### 核心代码
- `backend/llm/client.py` — LLM 客户端，LangChain 升级主要影响点
- `backend/agents/workflow.py` — LangGraph 工作流定义
- `backend/agents/nodes.py` — Agent 节点函数

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- 无特殊可复用组件 — 此阶段是基础设施升级

### Established Patterns
- 项目使用 pyenv 管理 Python 版本
- 项目使用 requirements.txt 管理依赖
- 测试使用 pytest 框架

### Integration Points
- `backend/llm/client.py` 导入 `langchain_core.messages` 和 `langchain_core.prompts`
- `backend/agents/workflow.py` 导入 `langgraph.graph.StateGraph`
- `backend/config.py` 使用 pydantic-settings

</code_context>

<deferred>
## Deferred Ideas

### Reviewed Todos (not folded)
- 无 — 此阶段聚焦 Python 和 LangChain 升级，其他改进属于未来阶段

</deferred>

---

*Phase: 01-python-langchain-upgrade*
*Context gathered: 2026-04-16*
