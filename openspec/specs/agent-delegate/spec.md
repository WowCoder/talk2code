# agent-delegate Specification

## Purpose
TBD - created by archiving change adopt-openhands-extensibility. Update Purpose after archive.
## Requirements
### Requirement: 子任务类型定义
系统 SHALL 定义 `TaskType` 枚举，包含以下值：`research`（信息收集）、`code`（编码实现）、`review`（代码审查）。TeamLeader 产出的 `plan.tasks` 中每个任务项 MUST 可携带 `type` 字段（默认 `code`）。

#### Scenario: TL 产出混合类型子任务
- **WHEN** TeamLeader 分析需求后产出的 plan 包含 `[{"type": "research", "description": "确定路由方案"}, {"type": "code", "file": "index.html"}]`
- **THEN** 系统按 `implementation_order` 顺序执行，research 任务使用轻量 LLM 调用，code 任务使用 ToolCallLoop

### Requirement: 按任务类型选择执行策略
`coder_node` SHALL 根据子任务的 `TaskType` 选择不同的执行策略：
- `research`: 单次 LLM 调用（`client.chat()`），结果写入 `role_outputs`，不分配工具权限
- `code`: 现有 `ToolCallLoop` 完整流程（工具调用 + 验证闭环）
- `review`: 调用 `_review_single_file()` 对已完成文件做审查

#### Scenario: research 任务不触发工具调用
- **WHEN** 子任务类型为 `research`
- **THEN** 系统调用 `client.chat()` 获取答案，不进入 ToolCallLoop，不消耗工具调用配额

#### Scenario: code 任务行为与现在一致
- **WHEN** 子任务类型为 `code`（或未指定 type）
- **THEN** 系统行为与当前 `coder_node` 完全一致（ToolCallLoop + verify + repair）

### Requirement: 任务间上下文传递
已完成子任务的产出 SHALL 注入到后续子任务的上下文中。research 任务的结果 MUST 追加到 `dialogue_history` 作为系统消息。code 任务产出的文件 MUST 出现在后续任务的 `completed_files` 摘要中。

#### Scenario: research 结果被后续 code 任务使用
- **WHEN** research 子任务完成并返回 "建议使用 Hash-based 路由"
- **THEN** 后续 code 任务的系统 Prompt 包含该建议作为参考上下文

### Requirement: 向后兼容固定流程
当 `plan.tasks` 未包含 `type` 字段（即全部为默认 `code` 类型）时，系统 SHALL 行为与当前固定 pipeline 完全一致。

#### Scenario: 旧格式 plan 兼容
- **WHEN** TL 产出的 plan 中 tasks 不包含 `type` 字段（如旧版 TL prompt 产出）
- **THEN** 所有任务按 `code` 类型处理，执行流程与重构前完全一致

