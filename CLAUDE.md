# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 快速开始

```bash
cd backend
pip install -r requirements.txt
python app.py
```

访问 http://localhost:5000/login.html，使用测试账号：test / 123456

## 架构概览

**技术栈**：Flask 后端 + 原生前端 (HTML/JS/Tailwind) + SQLite + SSE 实时通信

**核心流程**：用户输入需求 → AI 多智能体协同 (研究员→产品经理→架构师→工程师) → SSE 推送对话和代码 → 前端实时渲染

**后端结构** (`backend/`):
- `app.py`: Flask 主程序，包含 API 路由、AI 智能体逻辑、SSE 推送
- `models.py`: SQLAlchemy 数据库模型 (User, Requirement)
- `config.py`: 配置管理 (数据库、JWT、SSE、AI 模型)
- `utils.py`: 工具函数 (密码加密、SSE 消息格式)
- `llm_client.py`: 基础 LLM 客户端 (DashScope API)
- `langchain_client.py`: LangChain 封装的 LLM 客户端 (支持会话记忆)

**前端结构** (`frontend/`):
- `login.html`: 登录/注册页
- `index.html`: 首页，需求输入和列表
- `detail.html`: 需求详情页，AI 对话 + CodeMirror 编辑器

## 关键设计

**AI 智能体**：app.py 中的 `generate_requirement_data()` 函数实现了 4 个角色的模拟对话，通过 SSE 实时推送。生产环境需替换为真实 AI 调用。

**SSE 推送**：使用 `flask.Response` with `text/event-stream`，消息格式为 `data: {...}\n\n`，前端自动重连。

**代码生成**：`CodeGenerator` 类根据应用类型 (待办/计算器/笔记) 生成不同的模板代码，通过 `speed` 参数控制打字机速度。

**会话记忆**：`langchain_client.BailianLLM` 使用 LangChain 的 `ConversationBufferMemory` 实现多轮对话记忆。

## 常见问题修复

**LangChain 导入冲突**：`langchain_client.py:86` 使用局部导入 `from langchain.schema import HumanMessage` 避免与回退逻辑冲突。
