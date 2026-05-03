# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 快速开始

```bash
cd backend
pip install -r requirements.txt
python app.py
```

访问 http://localhost:5001/login.html，使用测试账号：test / 123456

## 架构概览

**技术栈**：Flask 后端 + 原生前端 (HTML/JS/Tailwind) + SQLite + SSE 实时通信

**核心流程**：用户输入需求 → AI 多智能体协同 (研究员→产品经理→架构师→工程师) → SSE 推送对话和代码 → 前端实时渲染

**后端结构** (`backend/`):
- `app.py`: Flask 主程序，包含 API 路由、AI 智能体逻辑、SSE 推送
- `models.py`: SQLAlchemy 数据库模型 (User, Requirement)
- `config.py`: 配置管理 (数据库、JWT、SSE、AI 模型)
- `utils.py`: 工具函数 (密码加密、SSE 消息格式)
- `llm/client.py`: 统一 LLM 客户端，支持 OpenAI 兼容和 Anthropic 兼容两种协议，通过 LLM_PROVIDER 配置切换
- `agents/`: LangGraph 多智能体节点和工作流
- `services/`: 需求处理、SSE 推送、任务队列服务

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
