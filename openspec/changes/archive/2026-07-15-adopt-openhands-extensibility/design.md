## Context

Talk2Code 的核心运行时 (`ToolCallLoop`) 目前通过硬编码 `handler_map` 将工具名称映射到处理器方法。Skill 系统通过 `_get_craft_context()` 调用 `load_for_task()` 实现按需加载，但触发逻辑（需求文本匹配）散落在调用方。LangGraph 工作流是固定的 3 节点 pipeline（team_leader → coder → verify），不支持动态子任务委派。上下文压缩使用简单的最近 N 条截断 + token 预算压缩，关键消息可能被丢弃。

本设计借鉴 OpenHands 的扩展性模式，引入五个新能力：基于注册的工具分派、声明式 Skill 触发、Agent 委派模式、上下文标记保护、类型化事件。所有变更保持向后兼容，不修改外部 API。

## Goals / Non-Goals

**Goals:**
- 工具处理器通过装饰器/注册机制自注册，消除 `handler_map` 硬编码
- Skill 通过 `manifest.json` 声明触发条件，运行时自动扫描匹配
- LangGraph 工作流支持 `research`/`code`/`review` 多类型子任务委派
- 上下文压缩支持重要消息标记，保护关键决策和 plan 不被丢弃
- 工具事件从松散 `dict` 迁移到 Pydantic 模型，提升类型安全

**Non-Goals:**
- 不引入 Docker 沙箱（Talk2Code 只做前端代码生成，Playwright 预览已足够）
- 不重写前端（Vue 3 当前实现工作良好）
- 不引入 Socket.IO（SSE 对当前场景足够）
- 不支持全语言 Shell 执行（非 Web 前端生成不在范围内）
- 不实现 Micro-Agent 架构（过度设计，3 角色模型足够）

## Decisions

### D1: ToolHandler 基类 + 装饰器注册

**选择**：定义 `ToolHandler` 抽象基类，通过 `@register_tool("tool_name")` 装饰器实现自注册。

**替代方案**：
- ❌ 保持 handler_map：扩展性差，每加工具需改 runtime.py
- ❌ 完全模仿 OpenHands Action/Observation：过重，Talk2Code 的工具数 < 15 个
- ✅ ToolHandler 基类 + 装饰器：轻量，Pythonic，保持增量迁移路径

**理由**：现有 `ToolDefinition` dataclass + `ToolRegistry` 已经提供了注册基础设施。只需将 handler 从裸函数改为带 `execute()` 方法的类，并用装饰器替代手动 `registry.register()` 调用。

### D2: Skill Manifest 文件约定

**选择**：在 `prompts/skills/<skill-name>/` 目录下放置 `manifest.json`，包含 `trigger`（关键词正则）、`type`（task/knowledge/repository）、`priority`（匹配优先级）字段。`SkillLoader` 在首次请求时扫描所有 manifest 并建立索引。

**替代方案**：
- ❌ YAML manifest：需要额外依赖，JSON 更容易解析
- ❌ 代码内注册：失去声明式的好处，社区贡献门槛高
- ✅ JSON manifest 文件：零依赖，约定优于配置，对齐 OpenHands Plugin 思路

**理由**：与 OpenHands 的 Skill 目录结构对齐，便于未来支持社区 Skill 市场。JSON 格式简单，`manifest.json` 与 `SKILL.md` 放在同一目录，符合直觉。

### D3: LanGraph 子图委派

**选择**：扩展 `AgentState` 增加 `subtasks` 字段（`List[Subtask]`），`TaskType` 枚举区分 `research`/`code`/`review`。在 `coder_node` 中根据 `TaskType` 选择不同的执行策略（研究→轻量 LLM 调用，编码→ToolCallLoop，审查→CodeReview）。子任务按 `implementation_order` 拓扑排序串行执行。

**替代方案**：
- ❌ 动态图修改：LangGraph 不原生支持运行时添加节点
- ❌ 完全 Micro-Agent：过度设计，Talk2Code 不需动态 spawn Agent
- ✅ 静态子图 + 运行时策略选择：简单可控，复用现有 ToolCallLoop

**理由**：Talk2Code 的任务分解已经由 TeamLeader 的 `implementation_order` 完成。子任务委派是在执行层根据任务类型选择策略，而非在编排层动态创建 Agent。这与当前架构兼容性最好。

### D4: 上下文消息标记

**选择**：在对话消息 dict 中增加 `preserve: bool` 标记字段。`ContextCompactor.maybe_compact()` 压缩时跳过 `preserve=True` 的消息。TL plan 消息、CompletionContract 状态注入、用户反馈自动标记为 `preserve=True`。

**替代方案**：
- ❌ OpenHands Condenser Pipeline：过重，Talk2Code 不需要多冷凝器链
- ❌ 独立的关键消息存储：引入额外的存储和同步复杂度
- ✅ 消息标记字段：最小改动，与现有 `hidden` 标记模式一致

**理由**：现有 `dialogue_history` 消息已有 `hidden` 标记用于前端隐藏。添加 `preserve` 标记对齐现有模式，改动集中在 `_build_messages()` 和 `ContextCompactor`。

### D5: Pydantic 事件模型

**选择**：新增 `backend/harness/events.py`，定义 `ToolEvent`（工具调用事件）、`IterationBatch`（迭代批量事件）、`ThinkingEvent` 等 Pydantic BaseModel。`ToolCallLoop` 内部使用这些模型而非松散 dict，序列化到 `dialogue_history` 时转为 dict（保持兼容）。

**替代方案**：
- ❌ TypedDict：没有运行时验证，错误难以排查
- ❌ dataclass：没有自动校验，序列化需手动处理
- ✅ Pydantic BaseModel：类型安全 + 自动校验 + 序列化简单

**理由**：Pydantic 已在项目中使用（`config.py` 的 `BaseSettings`、models 中的 schema）。事件模型使用 Pydantic 可以提供编译时类型提示和运行时校验，同时保持与 JSON 列存储的兼容。

## Risks / Trade-offs

- **[Risk] 装饰器注册在模块导入时执行** → 确保工具模块在 ToolCallLoop 初始化前被导入。通过 `create_tool_registry()` 工厂函数统一管理导入顺序。
- **[Risk] Skill manifest 扫描增加首次请求延迟** → 扫描结果缓存到模块级变量，只在文件变更时重建索引。manifest 数量 < 10，IO 开销可忽略。
- **[Risk] 子任务委派中的 TaskType 枚举有限** → 初期只支持 research/code/review 三种，后续按需扩展。保留未知类型回退到默认 coder 行为。
- **[Risk] preserve 标记依赖开发者自觉设置** → 关键消息的 preserve 标记在框架层自动设置（TL plan、Contract 注入等），不依赖调用方或 LLM 产出。
- **[Trade-off] Pydantic 模型增加内存开销** → 只在 ToolCallLoop 内部使用 Pydantic 模型，序列化到数据库时转为 dict，不增加存储成本。

## Migration Plan

所有变更为**增量式**，不要求一次性切换：

1. **Phase 1（P0）**：tool-dispatch + skill-manifest + typed-events。改动集中在 `runtime.py`、`tools/`、`events.py`。现有测试覆盖这些路径，重构时保持测试通过。
2. **Phase 2（P1）**：context-condenser + agent-delegate。改动 `compactor.py` 和 `graph.py`/`nodes.py`。Agent 委派通过新增 `TaskType` 字段支持，默认行为不变。
3. **Phase 3（P2）**：Plugin 打包机制。基于 Phase 1/2 的基础设施，支持 `.talk2code-plugin/` 目录打包。

回滚策略：每个 Phase 独立提交，通过 `backend && pytest` 验证。如有问题，Git revert 即可恢复。

## Open Questions

1. **Skill manifest 的 `trigger` 是否需要支持正则以外的匹配方式**（如语义匹配）？初期用正则，后续可扩展 `trigger_type: regex | semantic`。
2. **Agent 委派的子任务是否需要独立的 LLM 上下文**（全新 system prompt）还是共用？初期共用（与当前 chat 模式一致），后续可支持独立上下文。
3. **Plugin 目录约定是否应该对齐 OpenHands 的 `.plugin/plugin.json` 还是自定义 `.talk2code-plugin/`？建议自定义以避免与 OpenHands 工具链冲突。
