# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 快速开始

```bash
python -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
cd backend && python app.py
```

访问 http://localhost:5001/login.html，使用测试账号：test / 123456

LLM 配置：复制 `backend/.env.example` 为 `backend/.env`，填入 API Key。通过 `LLM_PROVIDER` 切换协议（`openai_compatible` / `anthropic_compatible`）。

## 架构概览

**技术栈**：Flask 后端 + 原生前端 (HTML/JS/Tailwind) + SQLite + SSE 实时通信

**核心流程**：用户输入需求 → LangGraph 工作流 (Planner → ToolCoder) → ToolCallLoop 外部执行 → SSE 推送对话和代码 → 前端实时渲染

**后端结构** (`backend/`):
- `app.py`: Flask 主程序，API 路由、SSE 推送
- `config.py`: Pydantic 配置管理 (数据库、JWT、SSE、LLM)
- `models/`: SQLAlchemy 数据库模型 (User, Requirement)，含 `models.py` + `schema.py`
- `llm/client.py`: 统一 LLM 客户端，支持 OpenAI/Anthropic 双协议，配置驱动切换
- `harness/`: Agent 运行时框架，包含完整 6 层架构 + 执行引擎（见下方）
- `agents/`: 向后兼容重导出层，实际逻辑已全部迁入 `harness/`
- `services/`: SSE 传输层 (`sse_manager.py`)、任务调度 (`task_queue.py`)、应用胶水层 (`requirement_service.py`)
- `utils/`: 限流 (`rate_limiter.py`)、重试 (`retry.py`)、认证安全 (`security.py`)、SSE 格式化+时间戳 (`sse.py`)
- `skills/`: Skill 定义（`generic/SKILL.md`）

**Harness 框架** (`backend/harness/`)：
| 模块 | 文件 | 职责 |
|---|---|---|
| **Runtime** | `runtime.py` | ReAct 工具调用循环 — LLM 调用 → 工具执行 → Hook 触发 → 循环，是整个 Agent 的执行引擎 |
| **Graph** | `graph.py` | LangGraph 工作流定义 (planner → END) |
| 1 - Instructions | `instructions/prompts.py`, `nodes.py`, `assembler.py`, `compactor.py`, `craft_loader.py` | 提示词模板、Planner/Coder 节点函数、上下文组装/压缩、Skill 加载 |
| 2 - Tools | `tools/` | 工具注册表 (`registry.py`)、文件操作、代码生成/验证、Web 工具 |
| 3 - Environment | `environment/` | 权限管理 (`permissions.py`)、沙箱执行 (`sandbox.py`) |
| 4 - State | `state/` | AgentState 定义 (`agent_state.py`)、WorkspaceFS、Git 版本化、记忆存储 |
| 5 - Constraints | `constraints/hooks.py`, `checks.py` | Hook 管理器 + 统一约束检查（Craft 规则、安全、质量） |
| 6 - Observability | `observability/` | 链路追踪 (`tracer.py`)、成本统计、SSE 事件上报 (`sse_reporter.py`)、日志系统 (`logger.py`) |

**前端结构** (`frontend/`):
- `login.html`: 登录/注册页
- `index.html`: 首页，需求输入和列表
- `detail.html`: 需求详情页，AI 对话 + CodeMirror 编辑器
- `history.html`: 历史记录页
- `settings.html`: 设置页
- `js/`: 前端 JavaScript 模块

## 关键设计

**AI 智能体**：Harness 框架统一管理 Agent 全生命周期。`harness/graph.py` 定义 LangGraph 工作流 (planner → END)，`harness/runtime.py` 的 `ToolCallLoop` 是 ReAct 执行引擎（LLM 调用 → 工具执行 → Hook 触发 → 循环）。`services/requirement_service.py` 是应用胶水层，负责初始化 Harness、连接数据库、推送 SSE。

**SSE 推送**：`services/sse_manager.py` 负责传输层（队列管理、心跳、连接生命周期），`harness/observability/sse_reporter.py` 负责语义层（将 Agent 事件翻译为 SSE 消息）。消息格式为 `data: {...}\n\n`，前端自动重连。

**代码生成**：`harness/instructions/nodes.py` 中的 `tool_coder_node` 内部执行 ToolCallLoop 完成所有工具调用（文件操作、代码生成等），不再依赖 LangGraph 的迭代机制。

**LLM 配置**：通过 `.env` 中的 `LLM_PROVIDER` 切换 API 协议，支持 OpenAI 兼容接口和 Anthropic 兼容接口。

## 常见问题修复

**LangChain 模板花括号转义**：`harness/instructions/prompts.py` 中 `ChatPromptTemplate.from_messages()` 需要将 JSON 中的 `{` `}` 转义为 `{{` `}}`，否则新版 langchain-core 会报错。
