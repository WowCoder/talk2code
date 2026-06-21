## 1. 基础设施：harness 目录 + LLM function calling

- [x] 1.1 创建 `backend/harness/` 目录结构（6 子包 + `__init__.py` 公共接口）
- [x] 1.2 扩展 `LLMClient.chat_with_tools()` 支持 OpenAI function calling 协议
- [x] 1.3 扩展 `LLMClient.chat_with_tools()` 支持 Anthropic tool use 协议
- [x] 1.4 扩展 `LLMResponse` 增加 `tool_calls` 字段，支持解析两种协议的 tool call 格式
- [x] 1.5 单元测试：`chat_with_tools()` 请求格式、响应解析、tool_calls 提取

## 2. L2 工具层：ToolRegistry + 首批工具

- [x] 2.1 实现 `harness/tools/registry.py`：`ToolRegistry` + `ToolDefinition` + `ToolResult`
- [x] 2.2 实现 `harness/tools/file_tools.py`：`read_file` / `write_file` / `list_files` / `delete_file`
- [x] 2.3 实现 `harness/tools/code_tools.py`：`validate_html` / `lint_css` / `lint_js` / `execute_code`
- [x] 2.4 实现 `harness/tools/web_tools.py`：`search_docs` / `fetch_cdn_library`
- [x] 2.5 迁移原 `prompts.py` 的 fallback 代码生成器到 `harness/tools/code_fallback.py`
- [x] 2.6 单元测试：`ToolRegistry` 注册/查询/执行，每个 tool handler 的入参/出参

## 3. L2 工具层：ToolCallLoop + LangGraph 工作流

- [x] 3.1 实现 `backend/agents/tool_loop.py`：`ToolCallLoop` ReAct 循环（最大 10 轮、连续 3 轮无进展终止）
- [x] 3.2 重写 `backend/agents/nodes.py`：保留 `planner_node`，新增 `tool_coder_node` + `tool_executor_node`
- [x] 3.3 重写 `backend/agents/workflow.py`：图结构改为 `planner → tool_coder ↔ tool_executor → END`
- [x] 3.4 实现 `tool_coder_node` 的条件路由：有 tool_calls → executor，无 → END
- [x] 3.5 单元测试：`ToolCallLoop` 正常完成/最大迭代/连续无进展终止
- [x] 3.6 集成测试：完整工作流 Planner → 多轮工具调用 → 完成

## 4. L4 状态层：WorkspaceFS + Git + Checkpoint

- [x] 4.1 实现 `harness/state/workspace.py`：`WorkspaceFS` 含 `_validate()` 路径穿越防护、子目录支持
- [x] 4.2 实现 `harness/state/versioning.py`：`GitVersioning` 自动 commit、log、rollback
- [x] 4.3 实现 `harness/state/checkpoint.py`：`CheckpointManager` 序列化/恢复 AgentState
- [x] 4.4 迁移 `backend/agents/state.py` 到 `harness/state/agent_state.py` 并增加新字段
- [x] 4.5 在 `RequirementService` 中集成 WorkspaceFS 初始化 + Checkpoint 恢复
- [x] 4.6 单元测试：`WorkspaceFS` 路径穿越拒绝、子目录创建、文件隔离
- [x] 4.7 单元测试：`GitVersioning` commit/log/rollback
- [x] 4.8 集成测试：中断后从 Checkpoint 恢复继续执行

## 5. L4 状态层：MemoryStore 长期记忆

- [x] 5.1 在 `models/models.py` 中新增 `AgentMemory` 表
- [x] 5.2 实现 `harness/state/memory_store.py`：`extract_memories()` LLM 驱动记忆提取
- [x] 5.3 实现 `recall()` 两阶段检索（≤10 条纯 LLM，>10 条 embedding + LLM）
- [x] 5.4 实现 `decay()` 重要性衰减 + `last_accessed_at` 更新 + `access_count` 递增
- [x] 5.5 实现 `remember()` 冲突检测和重要性更新
- [x] 5.6 在 `HookManager.ON_TASK_COMPLETE` 中触发 `extract_memories()`
- [x] 5.7 单元测试：记忆提取、检索、衰减、冲突处理
- [x] 5.8 集成测试：跨会话记忆召回验证

## 6. L3 环境层：权限 + 沙箱

- [x] 6.1 实现 `harness/environment/permissions.py`：`PermissionManager` 三级权限
- [x] 6.2 实现 `harness/environment/sandbox.py`：`SandboxExecutor` subprocess 隔离
- [x] 6.3 实现 `harness/environment/isolation.py`：用户会话隔离
- [x] 6.4 新增 `POST /api/requirements/<id>/permission` 端点：接收用户审批决策
- [x] 6.5 在 ToolCallLoop 中集成 `PermissionManager.check()` 前置检查
- [x] 6.6 前端权限审批 UI：确认卡片（30s 超时自动拒绝）
- [x] 6.7 SSE 新增 `permission_request` 事件
- [x] 6.8 单元测试：`PermissionManager` 权限判定、`SandboxExecutor` 超时/清理
- [x] 6.9 集成测试：权限审批端到端、沙箱执行隔离

## 7. L5 约束层：HookManager + 首批 Hook

- [x] 7.1 实现 `harness/constraints/hooks.py`：`HookManager` + 6 个生命周期
- [x] 7.2 实现 `harness/constraints/quality.py`：HTML/JS/CSS 语法检查 Hook + required_files Hook
- [x] 7.3 实现 `harness/constraints/security.py`：XSS/eval/innerHTML 检测 Hook
- [x] 7.4 实现 `harness/constraints/craft_enforcer.py`：anti_ai_slop Hook
- [x] 7.5 在 ToolCallLoop 中集成 Hook trigger（PRE/POST_TOOL_USE + ON_TASK_COMPLETE）
- [x] 7.6 实现约束失败升级策略：第 1 次反馈 Agent → 第 2 次加修复建议 → 第 3 次放过
- [x] 7.7 SSE 新增 `hook_check` 事件
- [x] 7.8 单元测试：`HookManager` 触发/注册，每个 Hook 的检查逻辑
- [x] 7.9 集成测试：Hook 失败 → Agent 修复循环

## 8. L1 指令层：ContextAssembler + Compactor + Skill

- [x] 8.1 实现 `harness/instructions/assembler.py`：`ContextAssembler` 动态上下文组装
- [x] 8.2 实现 `harness/instructions/compactor.py`：`ContextCompactor` P0-P3 分层压缩
- [x] 8.3 迁移 `prompts.py` 到 `harness/instructions/prompts.py`，去除 3 文件限制
- [x] 8.4 迁移 `craft_loader.py` 到 `harness/instructions/craft_loader.py`
- [x] 8.5 重写 Skill 加载器为单一通用 Skill：`skills/generic/SKILL.md`
- [x] 8.6 删除现有 5 个特定 Skill 和所有 `template.json` 文件
- [x] 8.7 更新 `_generate_clarify_questions()` prompt 增加视觉风格维度
- [x] 8.8 单元测试：`ContextAssembler` 组装逻辑、`ContextCompactor` 压缩保留
- [x] 8.9 测试：确认不需要的文件已删除、不需保留的兼容代码已清理

## 9. L2 补充：生成后继续对话流程

- [x] 9.1 改造 `POST /api/requirements/<id>/chat` 端点：跳过 Planner，直接进入 Coder ReAct 循环
- [x] 9.2 实现对话上下文加载 + 压缩（`ContextCompactor.maybe_compact()`）
- [x] 9.3 共用 `ToolCallLoop`（首次生成和后续修改用同一实现，仅初始状态不同）
- [x] 9.4 Chat 响应返回更新后的 code_files + updated_files 列表
- [x] 9.5 集成测试：首次生成 → 完成 → chat 修改代码 → 验证 Hook 仅检查修改的文件

## 10. L6 观测层：Tracer + CostTracker

- [x] 10.1 实现 `harness/observability/tracer.py`：`Tracer` + `Trace`/`Span` 模型
- [x] 10.2 在 `models/models.py` 新增 `AgentTrace` 表持久化 Trace 数据
- [x] 10.3 在 planner_node / tool_coder_node / tool_executor_node 中埋点
- [x] 10.4 实现 `harness/observability/cost.py`：`CostTracker` 从 LLM response 提取 usage
- [x] 10.5 SSE 新增 `trace_summary` 事件
- [x] 10.6 前端观测面板：可折叠执行详情树 + 用量统计
- [x] 10.7 单元测试：`Tracer` span 嵌套/序列化、`CostTracker` 计算

## 11. L6 观测层：日志 + 指标

- [x] 11.1 迁移 `utils/logger.py` 到 `harness/observability/logger.py`，配置 4 类日志文件
- [x] 11.2 实现日志轮转（按天 + 50MB）+ archive/ gzip 压缩
- [x] 11.3 实现 `GET /api/metrics` Prometheus 指标端点
- [x] 11.4 前端 Token 用量展示（详情页底部状态栏）
- [x] 11.5 确认日志输出到 `{项目根目录}/logs/` 目录

## 12. 服务层 + 路由层集成

- [x] 12.1 重写 `services/requirement_service.py` 集成 `harness/` 全部 6 层
- [x] 12.2 新增 `POST /api/requirements/<id>/permission` 端点
- [x] 12.3 更新 `GET /api/health` 健康检查：增加 ToolRegistry / Sandbox / MemoryStore 状态
- [x] 12.4 删除旧代码：`prompts.py`、`skill_loader.py`、`agents/state.py` 旧版、`utils/logger.py` 旧版
- [x] 12.5 确认 `config.py` 新增配置项：LOG_DIR、AGENT_LOG_RETENTION_DAYS、LOG_FILE_MAX_SIZE_MB

## 13. 前后端 SSE 事件集成

- [x] 13.1 实现 `harness/observability/sse_reporter.py`：SSE 事件统一管理
- [x] 13.2 `detail.html` 新增 tool_call / tool_result 工具卡片渲染
- [x] 13.3 `detail.html` 新增 thinking 流式文本渲染
- [x] 13.4 `detail.html` 新增 permission_request 确认卡片
- [x] 13.5 `detail.html` 新增 hook_check 状态标签
- [x] 13.6 `detail.html` 新增执行详情可折叠面板
- [x] 13.7 代码面板支持实时文件树更新（工具写入立即反映）

## 14. 端到端集成测试

- [x] 14.1 E2E：完整首次生成流程（Planner → Coder 工具循环 → Hook → complete）
- [x] 14.2 E2E：模糊需求 → clarify 含视觉风格 → 补充后完成生成
- [x] 14.3 E2E：首次生成 → chat 修改 → 多次修改
- [x] 14.4 E2E：工具调用中的权限审批流程
- [x] 14.5 E2E：Hook 失败 → Agent 自动修复 → 成功
- [x] 14.6 E2E：断点恢复（模拟进程中断后重新执行）
- [x] 14.7 E2E：跨会话记忆（用户偏好在新需求中生效）
- [x] 14.8 前端观测面板渲染验证
- [x] 14.9 所有现有测试确认通过（或更新到新架构后通过）
