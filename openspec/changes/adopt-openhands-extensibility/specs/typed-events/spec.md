## ADDED Requirements

### Requirement: 工具事件 Pydantic 模型
系统 SHALL 定义以下 Pydantic BaseModel 用于类型化工具事件：
- `ToolCallEvent`: 包含 `name` (str), `arguments` (dict), `display_label` (str), `success` (bool)
- `IterationBatchEvent`: 包含 `iteration` (int), `coder_name` (str), `thinking_preview` (str), `agent_text` (str), `tools` (List[ToolCallEvent]), `content` (str)
- `ThinkingEvent`: 包含 `name` (str), `content` (str)

#### Scenario: ToolCallEvent 校验失败
- **WHEN** 代码尝试创建 `ToolCallEvent(name=123)`（name 应为 str）
- **THEN** Pydantic 抛出 `ValidationError`，阻止无效数据进入事件流

### Requirement: ToolCallLoop 内部使用 Pydantic 模型
`ToolCallLoop.run()` 内部迭代中产生的工具事件 SHALL 使用 Pydantic 模型表示。`batch_tools` 列表中的元素 MUST 为 `ToolCallEvent` 实例。

#### Scenario: 批量事件类型安全
- **WHEN** ToolCallLoop 完成一轮迭代，构建 `IterationBatchEvent`
- **THEN** `batch_event.tools` 为 `List[ToolCallEvent]`，IDE 可提供自动补全和类型检查

### Requirement: 序列化到对话历史
Pydantic 事件模型 SHALL 提供 `.to_dict()` 方法，将模型序列化为 dict 以存入 `dialogue_history` 和数据库 JSON 列。`to_dict()` 输出 MUST 与当前 `batch_tools` dict 格式兼容。

#### Scenario: 数据库存储兼容
- **WHEN** `IterationBatchEvent.to_dict()` 被调用并写入数据库
- **THEN** 写入的 JSON 结构与重构前一致，前端可正常解析展示

### Requirement: SSE 事件兼容
`SSEReporter.iteration_batch()` SHALL 接受 `IterationBatchEvent` 作为参数（替代当前的松散 dict）。方法内部 MUST 将 Pydantic 模型序列化为 JSON 后通过 SSE 发送。

#### Scenario: SSE 推送行为不变
- **WHEN** ToolCallLoop 通过 SSEReporter 推送迭代批量事件
- **THEN** 前端收到的 SSE 消息格式与重构前一致，无需前端改动

### Requirement: 向后兼容旧 dict 格式
在 `_build_messages()` 和 `on_iteration` 回调中，对话历史中的事件仍以 dict 形式存在。从 `dialogue_history` 读取的 dict MUST 可通过 `IterationBatchEvent.from_dict()` 类方法反序列化为 Pydantic 模型（用于需要类型化操作的场景）。

#### Scenario: 页面刷新后恢复事件
- **WHEN** 用户刷新页面，前端从数据库加载对话历史
- **THEN** 对话历史中的 dict 可被后端反序列化为 Pydantic 模型进行后续处理
