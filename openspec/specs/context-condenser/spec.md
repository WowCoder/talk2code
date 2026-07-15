# context-condenser Specification

## Purpose
TBD - created by archiving change adopt-openhands-extensibility. Update Purpose after archive.
## Requirements
### Requirement: 消息保留标记
对话历史消息 dict SHALL 支持可选的 `preserve: bool` 字段（默认 `False`）。当 `preserve=True` 时，`ContextCompactor.maybe_compact()` MUST 跳过该消息，不参与截断或压缩。

#### Scenario: 保留消息不被压缩
- **WHEN** 对话历史包含 50 条消息，其中 3 条标记 `preserve=True`，ContextCompactor 触发压缩
- **THEN** 3 条保留消息原样保留在输出中，其余 47 条参与压缩

### Requirement: 框架层自动标记关键消息
以下类型的消息 SHALL 由框架自动标记 `preserve=True`：
- TeamLeader plan 消息（`role: "agent", plan: {...}`）
- CompletionContract 状态注入消息（`role: "system", name: "System"`，包含合同状态）
- 用户反馈消息（`role: "user"`，包含 `[用户补充说明]` 前缀）

#### Scenario: TL plan 自动保留
- **WHEN** ToolCallLoop 构建消息列表供 LLM 调用
- **THEN** TL 的 plan 消息始终出现在上下文中，不受压缩影响

#### Scenario: 用户反馈不被压缩丢弃
- **WHEN** 用户在 Chat 模式中提供了修改意见，对话历史超过 token 预算
- **THEN** 用户反馈消息被保留，不参与压缩

### Requirement: 压缩预算分配
`ContextCompactor` SHALL 在计算压缩预算时，先扣除所有 `preserve=True` 消息的 token 估算值，剩余预算分配给可压缩消息。如果保留消息本身已超过预算，MUST 记录 WARNING 并保留所有 preserve 消息，不压缩非保留消息。

#### Scenario: 保留消息在预算内
- **WHEN** 总预算 56000 tokens，3 条保留消息共 3000 tokens
- **THEN** 剩余 53000 tokens 用于压缩其他消息

#### Scenario: 保留消息超过预算
- **WHEN** 总预算 56000 tokens，保留消息共 58000 tokens
- **THEN** 记录 WARNING 日志，保留全部 preserve 消息，非保留消息全部丢弃

### Requirement: 向后兼容
未设置 `preserve` 标记的现有消息 SHALL 被视为 `preserve=False`，行为与重构前一致。

#### Scenario: 旧消息行为不变
- **WHEN** 对话历史中的所有消息均未设置 `preserve` 字段
- **THEN** 压缩行为与当前 `ContextCompactor` 完全一致

