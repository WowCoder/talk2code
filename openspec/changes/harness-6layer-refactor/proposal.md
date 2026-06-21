## Why

Talk2Code 当前架构是一个"Prompt-based 代码生成器"——LLM 单次输出 JSON 代码，无法迭代验证、无法使用工具、无法跨会话记忆用户偏好。Agent Harness Engineering 6 层架构提供了一个成熟的工程方法论来系统化解决这些问题：将 Agent 从"模型 + 提示词"升级为"模型 + 工具 + 沙箱 + 状态 + Hook + 观测"的完整运行时。

## What Changes

### L1 指令层：动态上下文组装
- 新增 `ContextAssembler`，根据需求类型动态选择 Craft 规则和 Skill 知识，替代静态 Prompt 模板
- 新增上下文压缩机制（`ContextCompactor`），按 P0-P3 优先级分层压缩，防止工具调用循环导致 token 超限
- 扩展交互式澄清流程，增加视觉风格偏好发现
- Skill 从 5 个特定应用模板（todo/calculator/note/calendar）改为单一通用 Skill，提供前端开发最佳实践和易错点知识
- **BREAKING**: 删除 `prompts.py`、`skill_loader.py` 旧实现，删除所有 `skills/*/template.json` 硬编码模板

### L2 工具层：ReAct 工具调用循环
- 新增 `ToolRegistry` 工具注册表，首批工具包括文件操作（read/write/list/delete）、代码验证（validate_html/lint_css/lint_js/execute_code）、Web 工具（search_docs/fetch_cdn_library）
- `LLMClient` 扩展 `chat_with_tools()` 方法，支持 OpenAI function calling 和 Anthropic tool use 双协议
- Agent 从单次 JSON 输出改为 ReAct 工具调用循环（Think → Tool Call → Observe → 迭代 → Done）
- LangGraph 工作流从线性 `planner → coder → END` 改为 `planner → tool_coder ↔ tool_executor → END`
- 新增生成后继续对话流程，与首次生成共用 ToolCallLoop
- **BREAKING**: LLM 生成代码从 JSON 输出改为工具调用（write_file）；删除 `generate_fallback_code()` 硬编码模板

### L3 环境层：安全边界
- 新增 `PermissionManager` 三级权限模型（只读/写入/执行），Level 2 执行需用户审批
- 新增 `SandboxExecutor` 代码执行沙箱（subprocess + 超时 + 内存限制）
- `WorkspaceFS` 增加三层文件隔离（user_id 路径隔离 + `_validate()` 防路径穿越 + TaskQueue 并发控制）
- **BREAKING**: Agent 代码验证从无到有，可能暴露之前被忽略的代码问题

### L4 状态层：状态外置
- `WorkspaceFS` 运行时文件系统，每个需求独立工作目录
- `GitVersioning` 每次代码变更自动 commit，支持 diff 和回滚
- `MemoryStore` LLM 驱动的长期记忆提取和两阶段检索（≤10 条 LLM 直接判读，>10 条 embedding + LLM 精排）
- `CheckpointManager` 持久化检查点，支持断点恢复
- **BREAKING**: AgentState 从内存 TypedDict 变为持久化状态

### L5 约束层：Hook 系统
- 新增 `HookManager` 5 个生命周期 Hook（PRE/POST_TOOL_USE、PRE/POST_LLM_CALL、ON_ERROR、ON_TASK_COMPLETE）
- 首批 Hook：HTML/CSS/JS 语法检查、XSS/eval 安全检测、Craft 规则强制执行、AI 坏味道检测
- 原则：成功静默，失败喧哗（检查通过不污染 Context，失败才反馈给 Agent 修复）
- **BREAKING**: Craft 规则从"建议注入 Prompt"变为"强制 Hook 检查"

### L6 观测层：可观测性
- 新增 `Tracer` 链路追踪（Planner/Coder/工具调用各环节耗时）
- 新增 `CostTracker` Token 用量和成本统计（从 LLM API response 提取 usage）
- SSE 事件体系扩展（新增 tool_call/tool_result/thinking/hook_check/permission_request/trace_summary）
- 新增日志系统（app/agent/llm/access 四类日志，按天轮转，Prometheus 指标）
- 前端新增 Agent 执行详情面板和工具调用卡片

### 架构简化
- Multi-Agent 架构保持 Planner + ReAct Coder，Planner 负责结构化设计，Coder 负责工具调用实现
- 删除所有回滚兼容机制，旧代码直接删除不保留兼容层
- `harness/` 作为新核心目录，6 个子包对应 6 层架构

## Capabilities

### New Capabilities
- `agent-tool-loop`: Agent ReAct 工具调用循环，ToolRegistry 注册表，LLM function calling 支持
- `dynamic-context-assembly`: 动态上下文组装，按需加载 Craft/Skill/Memory，上下文压缩
- `execution-sandbox`: 代码执行沙箱，三级权限审批，文件路径隔离
- `state-persistence`: WorkspaceFS 文件系统，Git 版本化，长期记忆 LLM 提取和检索，Checkpoint 断点恢复
- `hook-system`: HookManager 生命周期管理，代码质量/安全/Craft 强制执行 Hook
- `agent-observability`: 链路追踪，成本统计，SSE 事件体系，日志系统，Agent 执行可见性
- `post-generation-chat`: 生成后继续对话修改代码，与首次生成共用工具调用循环
- `design-preference-discovery`: 交互式澄清流程扩展，增加视觉风格偏好发现

### Modified Capabilities
<!-- No existing specs to modify - this is the first full architecture change -->

## Impact

**修改的文件：**
- `backend/agents/nodes.py` — 重写，planner_node + tool_coder_node + tool_executor_node
- `backend/agents/workflow.py` — 重写，新图结构
- `backend/agents/tool_loop.py` — 新增，ToolCallLoop
- `backend/llm/client.py` — 扩展，chat_with_tools()
- `backend/services/requirement_service.py` — 重写，集成 harness 层
- `backend/app.py` — 新增权限审批端点，chat 端点改造

**新增的目录：**
- `backend/harness/instructions/` — L1 指令层
- `backend/harness/tools/` — L2 工具层
- `backend/harness/environment/` — L3 环境层
- `backend/harness/state/` — L4 状态层
- `backend/harness/constraints/` — L5 约束层
- `backend/harness/observability/` — L6 观测层

**删除的文件：**
- `backend/prompts.py` → 迁移到 `harness/instructions/prompts.py`
- `backend/craft_loader.py` → 迁移到 `harness/instructions/craft_loader.py`
- `backend/skill_loader.py` → 重写为通用 Skill 加载
- `backend/agents/state.py` → 迁移到 `harness/state/agent_state.py`
- `backend/utils/logger.py` → 迁移到 `harness/observability/logger.py`
- `backend/skills/*/template.json` → 删除（Agent 动态生成代码）
- `backend/skills/*/SKILL.md`（除 generic 外）→ 删除
