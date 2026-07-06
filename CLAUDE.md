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

**技术栈**：Flask 后端 + Vue 3 (TypeScript/Vite) 前端 + SQLite + SSE 实时通信

**核心流程**：用户输入需求 → IntentRouter 意图分流 → LangGraph v3 多节点编排 → 逐文件编码 + LGTM/LBTM 审查 → SSE 推送对话和代码 → 前端实时渲染

**LangGraph v3 工作流**（8 节点 + 条件路由）：
```
team_leader (需求分析 + 任务分解) → [复杂度路由]
  ├─ XS/S → simple_coder (单文件 ToolCallLoop) → END
  └─ M/L → pm → architect → file_by_file_coder (逐文件编码 + CodeReview)
              → qa_reviewer → summarize → END
              ↑ fail          ↓ fail
              └── repair ─────┘
```

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
| **Runtime** | `runtime.py` | ReAct 工具调用循环 — LLM 调用 → 工具执行 → Hook 反馈 → 循环，是整个 Agent 的执行引擎 |
| **Graph** | `graph.py` | LangGraph v3 多节点编排工作流 (8 节点 + 条件路由) |
| 1 - Instructions | `instructions/prompts.py`, `nodes.py`, `file_coder.py`, `simple_coder.py`, `summarize.py`, `assembler.py`, `compactor.py`, `orchestrator.py`, `role_executor.py`, `craft_loader.py` | 提示词模板、8 个 LangGraph 节点函数（TeamLeader/PM/Architect/SimpleCoder/FileCoder/QA/Summarize/Repair）、逐文件编码 + LGTM/LBTM CodeReview、上下文组装/压缩、Skill 加载 |
| 2 - Tools | `tools/` | 工具注册表 (`registry.py`)、文件操作、代码生成/验证、Web 工具 |
| 3 - Environment | `environment/` | 权限管理 (`permissions.py`)、沙箱执行 (`sandbox.py`) |
| 4 - State | `state/` | AgentState 定义 (`agent_state.py`, 含 tasks/interfaces/implementation_order)、WorkspaceFS、Git 版本化、记忆存储 |
| 5 - Constraints | `constraints/hooks.py`, `checks.py` | Hook 管理器 + 统一约束检查（Craft 规则、安全、质量）+ Hook 失败反馈链路 |
| 6 - Observability | `observability/` | 链路追踪 (`tracer.py`)、成本统计、SSE 事件上报 (`sse_reporter.py`)、日志系统 (`logger.py`) |

**前端结构** (`frontend-vue/`):
- Vue 3 + TypeScript + Vite 构建
- `src/components/`: 组件（detail/PanelTabs、Preview、CodeView 等）
- `src/views/`: 页面视图
- 构建产物输出到 `frontend-vue/dist/`，由 Flask 直接托管

## 关键设计

**AI 智能体**：Harness 框架统一管理 Agent 全生命周期。`harness/graph.py` 定义 LangGraph v3 多节点编排工作流（team_leader → [复杂度路由] → simple_coder(XS/S) / pm→architect→file_by_file_coder→qa→summarize(M/L)），`harness/runtime.py` 的 `ToolCallLoop` 是 ReAct 执行引擎（LLM 调用 → 工具执行 → Hook 反馈 → 循环）。`services/requirement_service.py` 是应用胶水层，负责初始化 Harness、连接数据库、推送 SSE。

**逐文件编码 + CodeReview**（期三核心特性）：`file_coder.py` 对 implementation_order 中的每个文件：构建 CodingContext（设计+任务+已完成文件+接口契约）→ ToolCallLoop 生成代码 → LGTM/LBTM 审查（6 维度）→ LBTM 则重写（最多 3 次）。Hook 失败结果实时注入 LLM 上下文，Agent 能"看到"验证错误并主动修复。ContextCompactor P0-P3 分层压缩防止长对话上下文溢出。

**SSE 推送**：`services/sse_manager.py` 负责传输层（队列管理、心跳、连接生命周期），`harness/observability/sse_reporter.py` 负责语义层（将 Agent 事件翻译为 SSE 消息）。消息格式为 `data: {...}\n\n`，前端自动重连。

**代码生成**：`harness/instructions/nodes.py` 中的各节点函数通过 LangGraph 编排执行。`file_by_file_coder_node` 内部调用 ToolCallLoop 完成逐文件编码和 CodeReview，`simple_coder_node` 用于 XS/S 简单模式。不再依赖外部硬编码分支判断复杂度。

**LLM 配置**：通过 `.env` 中的 `LLM_PROVIDER` 切换 API 协议，支持 OpenAI 兼容接口和 Anthropic 兼容接口。

## 常见问题修复

**LangChain 模板花括号转义**：`harness/instructions/prompts.py` 中 `ChatPromptTemplate.from_messages()` 需要将 JSON 中的 `{` `}` 转义为 `{{` `}}`，否则新版 langchain-core 会报错。

**f-string 中文引号问题**：Python f-string 中用 `"...“任务完成”..."` 会导致解析错误，应使用单引号包裹或转义：`'...“任务完成”...'`
