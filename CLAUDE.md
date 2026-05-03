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

**核心流程**：用户输入需求 → LangGraph 多智能体协同 (Planner → Coder) → SSE 推送对话和代码 → 前端实时渲染

**后端结构** (`backend/`):
- `app.py`: Flask 主程序，API 路由、SSE 推送
- `models.py`: SQLAlchemy 数据库模型 (User, Requirement)
- `config.py`: Pydantic 配置管理 (数据库、JWT、SSE、LLM)
- `llm/client.py`: 统一 LLM 客户端，支持 OpenAI/Anthropic 双协议，配置驱动切换
- `agents/nodes.py`: LangGraph 智能体节点 (Planner / Coder)
- `agents/workflow.py`: LangGraph 工作流定义
- `agents/state.py`: 智能体状态定义
- `prompts.py`: 智能体提示词模板
- `services/`: SSE 管理、任务队列、需求处理服务
- `utils/`: 日志、安全、限流、重试等工具模块

**前端结构** (`frontend/`):
- `login.html`: 登录/注册页
- `index.html`: 首页，需求输入和列表
- `detail.html`: 需求详情页，AI 对话 + CodeMirror 编辑器

## 关键设计

**AI 智能体**：使用 LangGraph 工作流编排 Planner → Coder 两个智能体节点，通过 `llm/client.py` 调用 LLM。

**SSE 推送**：使用 `flask.Response` with `text/event-stream`，消息格式为 `data: {...}\n\n`，前端自动重连。

**代码生成**：`agents/nodes.py` 中的 `engineer_node` 调用 LLM 生成代码，失败时使用 `prompts.py` 中的 `generate_fallback_code()` 模板兜底。

**LLM 配置**：通过 `.env` 中的 `LLM_PROVIDER` 切换 API 协议，支持 OpenAI 兼容接口和 Anthropic 兼容接口。

## 常见问题修复

**LangChain 模板花括号转义**：`prompts.py` 中 `SystemMessagePromptTemplate.from_template()` 需要将 JSON 中的 `{` `}` 转义为 `{{` `}}`，否则新版 langchain-core 会报错。
