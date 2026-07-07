# CLAUDE.md

## 项目概览

Talk2Code — Flask + Vue 3 前后端分离的 AI 编程助手。后端通过 LangGraph v3 多节点编排 Agent 工作流（需求分析 → 任务分解 → 逐文件编码 + 代码审查），SSE 实时推送对话和代码。LLM 通过 `llm/client.py` 统一调用，支持 OpenAI/Anthropic 双协议切换。

## 目录地图

```
backend/                   Python 后端
  app.py                     Flask 主程序，所有 API 路由 + SSE 端点（无蓝图，直接 @app.route）
  config.py                  Pydantic 配置管理（LLM/JWT/SSE/数据库），读取 .env
  models/                    SQLAlchemy 数据模型（models.py + schema.py）
  llm/
    client.py                统一 LLM 客户端（openai_compatible / anthropic_compatible）
  harness/                   Agent 运行时框架
    runtime.py                ReAct ToolCallLoop 执行引擎
    graph.py                  LangGraph v3 多节点编排工作流（8 节点 + 条件路由）
    harness_context.py        全局 Harness 上下文访问器
    instructions/             提示词模板 + 8 个节点函数（nodes.py / file_coder.py / simple_coder.py / ...）
    tools/                    工具注册表 registry.py + file/code/web/preview/edit 工具
    state/                    AgentState 定义 + WorkspaceFS + Git 版本化 + MemoryStore
    constraints/              Hook 管理器 + 约束检查
    observability/            链路追踪 + 成本统计 + SSE 事件上报 + 日志
    roles/                    角色定义
    communication/            通信层
    learning/                 学习模块
    experience/               经验模块
  services/                  SSE 传输层 + 任务调度 + 应用胶水层
  utils/                     限流 / 重试 / JWT 安全 / SSE 格式化
  skills/                    Skill 定义
  tests/                     测试（unit/ integration/ functional/ eval/）
  pytest.ini                 Pytest 配置
  requirements.txt           依赖
frontend-vue/               Vue 3 + TypeScript + Vite 前端（AI 生成，构建产物到 dist/）
start.sh                    开发/生产启动脚本（venv + pip + npm + gunicorn）
```

## 常用命令

```bash
# 环境准备
python3 -m venv venv && source venv/bin/activate
pip install -r backend/requirements.txt

# 启动开发
cd backend && python app.py                       # Flask 开发服务器（端口 5001）
source venv/bin/activate && bash start.sh          # 完整启动（venv + 前后端）

# 测试
cd backend && pytest                                 # 全量测试
cd backend && pytest tests/unit/test_xxx.py         # 单个测试文件

# 前端构建（前端为 AI 生成，一般不需要手动构建）
cd frontend-vue && npm install && npm run build
```

## 架构约定

- **LLM 调用必须走 `llm/client.py`**（`get_client()`），禁止直接调 provider API。通过 `LLM_PROVIDER` 环境变量切换协议。
- **新增 API 路由**：直接在 `app.py` 中用 `@app.route()` 装饰器，本项目不使用 Flask Blueprint。
- **新增 LangGraph 节点**：在 `harness/instructions/nodes.py` 中定义节点函数（签名：`def node_name(state: AgentState) -> Dict[str, Any]:`），在 `harness/graph.py` 中注册到工作流。
- **新增工具**：在 `harness/tools/` 对应模块中定义 handler，然后在 `registry.py` 的 `create_tool_registry()` 中注册。工具定义使用 `ToolDefinition` dataclass（name/description/parameters/handler/permission）。
- **新增服务**：业务逻辑放 `backend/services/`，路由 handler 放 `app.py`。
- **提示词模板**：放 `harness/instructions/prompts.py`，使用 `ChatPromptTemplate.from_messages()`。注意 JSON 中的 `{` `}` 必须转义为 `{{` `}}`。
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

## 注意事项

- 虚拟环境：`venv/`（Python 3.11+），所有命令在 `source venv/bin/activate` 后运行。
- 默认 SQLite 数据库（`vcd.db`、`atoms.db`），生产切 PostgreSQL 需要数据迁移。
- 测试账号：`test / 123456`，访问 http://localhost:5001/login.html。
- LLM 配置：复制 `backend/.env.example` 为 `backend/.env`，填入 API Key。
- **LangChain 模板花括号转义**：`prompts.py` 中 `ChatPromptTemplate.from_messages()` 的 JSON 示例需要将 `{` `}` 转义为 `{{` `}}`。
- **Python f-string 中文引号**：f-string 中不能出现 `"...“...”..."`，中文引号会与 f-string 引号冲突，改用单引号包裹或转义。
- Agent 保护参数（3 轮迭代 / ToolCallLoop 超时）不要随意调高，会直接增加 LLM 调用费用。
- 无 linter/formatter 配置，不主动运行 `flake8` 或 `black`。
