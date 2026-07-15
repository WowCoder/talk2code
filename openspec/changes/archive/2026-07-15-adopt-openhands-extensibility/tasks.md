## 1. Phase 0: 基础设施 — typed-events

- [ ] 1.1 创建 `backend/harness/events.py`，定义 `ToolCallEvent`、`IterationBatchEvent`、`ThinkingEvent` Pydantic 模型
- [ ] 1.2 为每个事件模型实现 `to_dict()` 序列化方法和 `from_dict()` 反序列化类方法
- [ ] 1.3 修改 `ToolCallLoop.run()` 中 `batch_tools` 构建逻辑，使用 `ToolCallEvent` 替代松散 dict
- [ ] 1.4 修改 `ToolCallLoop.run()` 中 `iteration_batch` 构建，使用 `IterationBatchEvent`
- [ ] 1.5 修改 `SSEReporter.iteration_batch()` 方法签名，接受 `IterationBatchEvent` 参数
- [ ] 1.6 运行 `cd backend && pytest tests/unit/` 确保类型化事件不破坏现有测试

## 2. Phase 0: 基础设施 — tool-dispatch

- [ ] 2.1 在 `backend/harness/tools/registry.py` 中新增 `ToolHandler` 抽象基类，定义 `execute(self, args, workspace, state) -> ToolResult`
- [ ] 2.2 在 `backend/harness/tools/registry.py` 中新增 `@register_tool("name")` 装饰器，内部调用 `registry.register()`
- [ ] 2.3 迁移 `FileToolHandler` 为 `ReadFileHandler` + `WriteFileHandler` + `ListFilesHandler` + `DeleteFileHandler` 四个 ToolHandler 子类
- [ ] 2.4 迁移 `EditToolHandler` 为 `EditFileHandler` ToolHandler 子类
- [ ] 2.5 迁移 `PreviewToolHandler` 为 `RunPreviewHandler` ToolHandler 子类
- [ ] 2.6 迁移 `CodeToolHandler` 为 `ValidateHtmlHandler` + `LintCssHandler` + `LintJsHandler` + `ExecuteCodeHandler` 四个 ToolHandler 子类
- [ ] 2.7 迁移 `web_tools.py` 中的 `search_docs` / `fetch_cdn_library` 为 ToolHandler 子类
- [ ] 2.8 重构 `ToolCallLoop._execute_tool()`：移除硬编码 `handler_map`，改为通过 `ToolRegistry` 获取 handler 并调用 `handler.execute()`
- [ ] 2.9 更新 `ToolCallLoop.__init__()`：移除 `_file_handler` / `_code_handler` / `_preview_handler` / `_edit_handler` 实例化，统一通过注册表访问
- [ ] 2.10 运行 `cd backend && pytest tests/unit/test_tool_registry.py tests/unit/test_tool_loop.py` 验证重构无回归

## 3. Phase 0: 基础设施 — skill-manifest

- [ ] 3.1 为 `prompts/skills/` 下每个已有 Skill 目录创建 `manifest.json`（game/snake, game/tetris, game/tank, dashboard, form 等）
- [ ] 3.2 创建 `backend/harness/instructions/skill_loader.py`，实现 `SkillLoader` 类：扫描 manifest、关键词匹配、缓存管理
- [ ] 3.3 `SkillLoader` 实现文件修改时间检测，支持热加载（manifest 变更时自动重建索引）
- [ ] 3.4 重构 `ToolCallLoop._get_craft_context()` 委托给 `SkillLoader`，保持方法签名兼容
- [ ] 3.5 运行 `cd backend && pytest tests/unit/` 确保 Skill 注入行为无回归

## 4. Phase 1: context-condenser

- [ ] 4.1 在 `ContextCompactor.maybe_compact()` 中增加 `preserve` 标记检测逻辑，跳过 `preserve=True` 的消息
- [ ] 4.2 修改压缩预算分配：先扣除保留消息 token 数，剩余分配给可压缩消息，并在保留消息超额时记录 WARNING
- [ ] 4.3 在 `ToolCallLoop._build_system_prompt()` 中，为 TL plan 消息自动设置 `preserve=True`
- [ ] 4.4 在 CompletionContract 状态注入和用户反馈消息中自动设置 `preserve=True`
- [ ] 4.5 运行 `cd backend && pytest tests/unit/` 验证压缩行为，确保保留消息不被丢弃

## 5. Phase 1: agent-delegate

- [ ] 5.1 在 `AgentState` 中新增 `TaskType` 枚举（research / code / review）和 `Subtask` 模型
- [ ] 5.2 修改 TeamLeader 的 `tl_analysis.md` prompt，支持产出带 `type` 字段的子任务列表
- [ ] 5.3 在 `coder_node` 中实现按 `TaskType` 选择执行策略：research → `client.chat()`，code → `ToolCallLoop`，review → `_review_single_file()`
- [ ] 5.4 实现任务间上下文传递：research 结果注入 dialogue_history，code 产出文件更新 completed_files 摘要
- [ ] 5.5 确保旧格式 plan（无 type 字段）回退到默认 code 行为，兼容现有流程
- [ ] 5.6 运行 `cd backend && pytest tests/unit/` 验证新旧 plan 格式均正常工作

## 6. Phase 2: Plugin 打包机制

- [ ] 6.1 定义 `.talk2code-plugin/plugin.json` 目录约定和 schema（name, version, skills, hooks, tools）
- [ ] 6.2 实现 `PluginLoader` 类：扫描插件目录、加载 skills/hooks/tools
- [ ] 6.3 支持通过环境变量 `T2C_PLUGINS_DIR` 指定插件目录
- [ ] 6.4 运行 `cd backend && pytest` 全量回归验证

## 7. 验证与清理

- [ ] 7.1 运行 `cd backend && pytest` 全量测试
- [ ] 7.2 运行 `cd frontend-vue && npm run build` 确保前端构建通过
- [ ] 7.3 清理废弃代码：移除 `_file_handler` / `_code_handler` / `_edit_handler` / `_preview_handler` 在 ToolCallLoop 中的残留实例化逻辑
- [ ] 7.4 更新 `README.md` 中的架构描述，反映新的工具注册和 Skill 声明式触发机制
