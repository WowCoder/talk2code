# fresh-context-evaluator Specification

## Purpose
TBD - created by archiving change agent-harness-hard-constraints. Update Purpose after archive.
## Requirements
### Requirement: 独立上下文评估
系统 SHALL 在所有文件编码完成后，使用全新的 LLM 上下文（不包含编码阶段的对话历史）进行独立评估。

#### Scenario: 评估上下文隔离
- **WHEN** Coder 节点完成所有文件的编码
- **THEN** Evaluator 启动时，对话历史仅包含：原始需求、SPEC、代码文件内容、`run_preview` 输出，不包含 Coder 阶段的任何 thinking/assistant/tool_call 记录

### Requirement: 真实浏览器执行验证
系统 SHALL 通过 headless 浏览器（`run_preview`）执行生成的 `index.html`，将浏览器 console 错误和 DOM 结构作为评估输入。

#### Scenario: 收集浏览器错误
- **WHEN** 生成的页面包含 JavaScript 运行时错误
- **THEN** Evaluator 收到浏览器 console 的完整错误信息（类型、消息、源文件、行号）

#### Scenario: 无 index.html 时跳过
- **WHEN** 生成的文件中不包含 `index.html`
- **THEN** Evaluator 在评估报告中标记"无法进行浏览器验证"，但仍基于代码静态分析完成评估

### Requirement: 只读权限
系统 SHALL 限制 Evaluator 只能使用 Read 类工具（`read_file`、`list_files`），不能使用 `write_file`、`edit_file` 或任何写操作。

#### Scenario: 尝试写入被拒绝
- **WHEN** Evaluator 尝试调用 `write_file` 或 `edit_file`
- **THEN** 工具调用被拒绝，返回"Evaluator 没有写入权限"

### Requirement: 结构化评估输出
系统 SHALL 要求 Evaluator 返回结构化 JSON 评估结果，包含 `verdict`（PASS/NEEDS_WORK）、`findings`（问题列表）、`score`（分维度评分）。

#### Scenario: 全部通过
- **WHEN** 所有验证维度均达标
- **THEN** 返回 `{"verdict": "PASS", "findings": [], "score": {"functionality": 8, "code_quality": 7, "ui_quality": 8}}`

#### Scenario: 发现问题
- **WHEN** 浏览器执行出现错误，或代码文件不符合 SPEC
- **THEN** 返回 `{"verdict": "NEEDS_WORK", "findings": ["index.html 未引入 js/app.js", "Grid.move() 在边缘情况下返回 undefined"], "score": {...}}`

### Requirement: 评估结果文件持久化
系统 SHALL 将 Evaluator 的评估结果写入 `.task/evaluator/result.json`，供后续流程和调试使用。

#### Scenario: 正常持久化
- **WHEN** Evaluator 完成评估
- **THEN** `.task/evaluator/result.json` 包含完整的评估结果 JSON

### Requirement: NEEDS_WORK 触发修复
系统 SHALL 在 Evaluator 返回 NEEDS_WORK 时，将 findings 传递给 Repair 节点进行定向修复。

#### Scenario: 触发修复循环
- **WHEN** Evaluator 返回 `verdict: "NEEDS_WORK"` 且修复轮次 < 3
- **THEN** 工作流路由到 Repair 节点，修复 prompt 包含 Evaluator 的 findings 列表

#### Scenario: 修复上限
- **WHEN** Evaluator 连续 3 次返回 NEEDS_WORK
- **THEN** 工作流强制结束，状态标记为 `completed_with_issues`

