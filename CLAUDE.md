# CLAUDE.md

## 项目概览

Talk2Code — Flask + Vue 3 前后端分离的 AI 编程助手。后端通过 LangGraph v5 多节点编排 Agent 工作流（需求分析 → 逐文件编码 + 质量验证），SSE 实时推送对话和代码。LLM 通过 `llm/client.py` 统一调用，支持 OpenAI/Anthropic 双协议切换。

## 常用命令

```bash
# 完整启动
source venv/bin/activate && bash start.sh
```

## 架构约定

- **LLM 调用必须走 `llm/client.py`**（`get_client()`），禁止直接调 provider API。通过 `LLM_PROVIDER` 环境变量切换协议。
- **新增 API 路由**：直接在 `app.py` 中用 `@app.route()` 装饰器，本项目不使用 Flask Blueprint。
- **新增 LangGraph 节点**：在 `harness/instructions/nodes.py` 中定义节点函数（签名：`def node_name(state: AgentState) -> Dict[str, Any]:`），在 `harness/graph.py` 中注册到工作流。
- **新增工具**：在 `harness/tools/` 对应模块中定义 handler，然后在 `registry.py` 的 `create_tool_registry()` 中注册。工具定义使用 `ToolDefinition` dataclass（name/description/parameters/handler/permission）。
- **新增服务**：业务逻辑放 `backend/services/`，路由 handler 放 `app.py`。
- **提示词模板**：放 `harness/instructions/prompts/` 目录下的 `.md` 文件，通过 `load_prompt()` 或 `load_prompt_template()` 加载。注意 JSON 中的 `{` `}` 必须转义为 `{{` `}}`。
- **SSE 推送**：传输层走 `services/sse_manager.py`，语义层（Agent 事件 → SSE 消息）走 `harness/observability/sse_reporter.py`。
- **配置**：所有配置通过 `config.py` 的 Pydantic `BaseSettings` 管理，新增配置项需要同步更新 `.env.example`。
- **临时文件**：一律输出到项目根目录的 `logs/` 或 `tmp/` 目录。
- **文件编码**：Python 文件头部添加 `# -*- coding: utf-8 -*-`。

## 修改后验证

- **修改 `app.py` 路由/API** → `cd backend && python -c "from app import app; print('OK')"`
- **修改 LangGraph 节点/工作流** → `cd backend && pytest tests/unit/`
- **修改 LLM 客户端** → `cd backend && pytest tests/unit/test_llm_client.py tests/unit/test_tool_loop.py`
- **修改工具注册/工具逻辑** → `cd backend && pytest tests/unit/test_tool_registry.py`
- **修改 Hook/约束检查** → `cd backend && pytest tests/unit/test_hooks.py`
- **修改 Harness 核心（runtime/graph/state）** → `cd backend && pytest tests/unit/`
- **任务完成** → `cd backend && pytest` + `cd frontend-vue && npm run build`

## 工作流程

- **Bug / 问题处理**：当用户抛出一个问题或 Bug 时，**先分析根因，给出解决方案，等用户确认后再修改代码**。禁止直接动手改代码。流程：分析 → 方案 → 确认 → 实施。
- **重启提醒**：修改完代码后，如果变更需要重启后端（Flask）或前端（Vite/Nginx）才能生效，**必须立即询问用户是否要重启**。不要等用户自己发现没生效再问。

## 注意事项

- 虚拟环境：`venv/`（Python 3.11+），所有命令在 `source venv/bin/activate` 后运行。
- **修改或重构代码时，同步删除不再使用的文件、类、函数、import，保持代码库整洁。** 除非明确要求保留兼容，否则无用代码不保留。
- **大量代码变动后，检查并同步更新 README.md**（目录结构、模块职责、架构描述等），确保文档与代码一致。
- **所有 LLM Prompt（角色、意图、审查、记忆、编码模板、任务包、Skills）统一放在 `backend/harness/instructions/prompts/` 下。** 新增 Prompt 时在此目录下创建对应子目录和 `.md` 文件，通过 `load_prompt()` 或 `load_prompt_template()` 加载，不写在 Python 代码内。
