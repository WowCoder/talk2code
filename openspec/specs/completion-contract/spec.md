# completion-contract Specification

## Purpose
TBD - created by archiving change agent-harness-hard-constraints. Update Purpose after archive.
## Requirements
### Requirement: 检查清单初始化
系统 SHALL 在 TeamLeader 节点输出 `implementation_order` 后，自动生成 `.task/contract.json` 文件，包含所有目标文件的完成状态。

#### Scenario: 正常初始化
- **WHEN** TeamLeader 节点完成，且 `implementation_order` 包含 `["js/storage.js", "js/game.js", "index.html"]`
- **THEN** 系统在工作区创建 `.task/contract.json`，内容包含所有三个文件的 `{"created": false, "validated": false}` 状态

#### Scenario: 空实现清单
- **WHEN** TeamLeader 输出 `implementation_order` 为空数组
- **THEN** 系统不创建 contract 文件，并记录警告日志

### Requirement: write_file 自动标记完成
系统 SHALL 在 `write_file` 工具执行成功后，通过 PreToolUse Hook 自动将对应文件在 contract 中标记为 `created: true`。

#### Scenario: 写入目标文件
- **WHEN** Agent 调用 `write_file` 成功写入 `js/game.js`
- **THEN** contract.json 中 `js/game.js.created` 自动更新为 `true`，无需 Agent 手动操作

#### Scenario: 写入非目标文件
- **WHEN** Agent 调用 `write_file` 写入了一个不在 contract 中的文件（如 `package.json`）
- **THEN** contract 不变，Hook 不报错

### Requirement: task_complete 阻断
系统 SHALL 在 Agent 声明任务完成时阻断，当且仅当 contract 中所有文件 `created: true` 时才允许通过。

#### Scenario: 有未完成文件时阻断
- **WHEN** Agent 尝试声明任务完成，但 contract 中仍有文件 `created: false`
- **THEN** PreToolUse Hook 返回阻断结果，包含未完成文件的列表，Agent 无法结束任务

#### Scenario: 全部完成时放行
- **WHEN** Agent 尝试声明任务完成，且 contract 中所有文件 `created: true`
- **THEN** PreToolUse Hook 不阻断，任务正常结束

### Requirement: 回读阻断
系统 SHALL 在 Agent 读取刚写入的文件时阻断，写入后 2 轮迭代内不允许 `read_file` 同一文件。

#### Scenario: 写入后立即读取
- **WHEN** Agent 在第 N 轮写入 `js/app.js`，并在第 N 或 N+1 轮调用 `read_file("js/app.js")`
- **THEN** PreToolUse Hook 阻断该 `read_file` 调用，返回原因说明

#### Scenario: 写入后间隔足够再读取
- **WHEN** Agent 在第 N 轮写入 `js/app.js`，并在第 N+3 轮调用 `read_file("js/app.js")`
- **THEN** PreToolUse Hook 不阻断，允许正常读取

#### Scenario: 读取非刚写入的文件
- **WHEN** Agent 调用 `read_file("js/game.js")`，且该文件最近一次写入距今超过 2 轮
- **THEN** PreToolUse Hook 不阻断

### Requirement: 阻断反馈信息
当 Hook 阻断操作时，系统 SHALL 返回明确的阻断原因和可行的替代建议。

#### Scenario: 回读阻断的反馈
- **WHEN** read_file 被阻断
- **THEN** 返回消息包含：被阻断的文件名、上次写入的轮次、提示文件完整无损

