## Why

Talk2Code 当前的工具注册、Skill 加载、Agent 编排都是硬编码实现（handler_map 分发、固定 3 节点 pipeline、散落的 Skill 触发逻辑），每扩展一个能力都需要修改核心代码。借鉴 OpenHands 的扩展性设计模式（Event-Driven 工具解耦、声明式 Skill 触发、Agent 委派），可以让 Talk2Code 从"固定能力工具"演化为"可扩展平台"，在保持核心流程简洁的前提下，大幅降低扩展成本。

## What Changes

- **工具执行解耦**：将 `ToolCallLoop._execute_tool()` 中的硬编码 `handler_map` 替换为基于注册的分发机制，新增工具只需注册 Handler 类，无需修改核心循环
- **Skill 声明式触发**：为 `prompts/skills/` 下的每个 Skill 添加 `manifest.json`（触发关键词 + 类型），运行时自动扫描匹配，替代当前的硬编码 `_get_craft_context()` 逻辑
- **Agent 委派模式**：扩展 LangGraph 工作流支持子任务委派（TL 的 plan 可包含 `research`/`code`/`review` 类型的子任务），替代固定 3 节点 pipeline
- **Context Condenser 增强**：支持"重要消息标记"，TeamLeader plan、CompletionContract 状态、用户反馈等关键消息不参与压缩
- **类型化事件系统**：将 ToolCallLoop 中的松散 dict（`batch_tools`、`iteration_batch` 等）替换为 Pydantic 模型，提升类型安全和可维护性

## Capabilities

### New Capabilities

- `tool-dispatch`: 基于注册的工具分派机制，Handler 自注册替代硬编码 handler_map
- `skill-manifest`: 声明式 Skill 触发系统，通过 manifest.json 定义关键词匹配和触发类型
- `agent-delegate`: Agent 子任务委派模式，扩展 LangGraph 工作流支持 research/code/review 多类型子任务
- `context-condenser`: 上下文压缩增强，支持重要消息标记保护关键上下文
- `typed-events`: 类型化的工具事件系统，Pydantic 模型替代松散 dict

### Modified Capabilities

<!-- 无现有 specs，均为新增能力 -->

## Impact

- **核心运行时**：`backend/harness/runtime.py`（ToolCallLoop — 工具分发重构，事件类型化）
- **工具系统**：`backend/harness/tools/registry.py`（注册表增强），`backend/harness/tools/*.py`（Handler 类化）
- **Skill 系统**：`backend/harness/instructions/prompts/skills/`（添加 manifest.json），`backend/harness/runtime.py`（`_get_craft_context` 重构）
- **工作流编排**：`backend/harness/graph.py`（支持委派子图），`backend/harness/instructions/nodes.py`（Agent 委派节点）
- **上下文管理**：`backend/harness/instructions/compactor.py`（增强压缩策略）
- **事件系统**：新增 `backend/harness/events.py`（类型化事件模型）
- **Breaking**：无外部 API 变更，内部重构保持向后兼容
