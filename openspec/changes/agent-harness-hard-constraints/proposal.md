## Why

Agent 当前依赖纯文本 prompt 注入来约束行为（如系统提示"缺少文件"、prompt 中写"不要重读文件"），但这种软约束在 LLM 面前没有强制力。Requirement #36（1024 小游戏，6 个目标文件）暴露了系统性失效：91 条消息后仍有 1 个文件未创建，系统 19 次提醒全被无视，Agent 陷入 lint 误报和重复读取的死循环。核心问题是**约束机制从设计上就不可靠**——prompt 是建议，不是规则。

## What Changes

- **Default-FAIL Contract**：基于 TeamLeader 输出的 `implementation_order` 自动生成检查清单文件（`.task/contract.json`），所有目标文件初始 `completed: false`。PreToolUse Hook 在 `write_file` 成功后自动标记完成，在全部完成前阻断 `task_complete` 声明
- **PreToolUse Hook 阻断**：新增 Hook 阻断不合理行为——刚写入的文件禁止 `read_file`（2 轮内）、文件未全部创建禁止声明任务完成，替代当前的系统文本提示注入
- **修复 lint_js 工具**：自动检测 ES Module 语法（`export`/`import`），使用 `--input-type=module` 参数调用 Node.js，消除误报导致的分心循环
- **write_file 只返回元数据**：`write_file` 结果不再包含文件完整内容（避免截断引发重复读取），只返回文件名、行数、字符数
- **Fresh-Context Evaluator**：用全新 LLM 上下文 + `run_preview` 真实浏览器执行做独立评估，替代当前 `tester_node`（LLM 模拟验证 AC）和 `summarize_node`（LLM 跨文件审查）两个伪验证节点
- **简化 LangGraph 工作流**：删除 PM / Architect / QA / Summarize 节点，TL 直接输出含设计规格的完整 Plan，工作流从 8 节点简化为 4 节点（TeamLeader → Coder → Verify → Repair）
- **edit_file 提升为默认编辑工具**：Prompt 中明确 `edit_file` 用于修改已有文件，`write_file` 仅用于创建新文件。**BREAKING**：现有 prompt 模板中修复指令的行为预期改变

## Capabilities

### New Capabilities

- `completion-contract`: Default-FAIL 检查清单 + PreToolUse Hook 硬约束，确保所有目标文件在任务结束前完成
- `fresh-context-evaluator`: 独立上下文评估器，基于真实浏览器执行 (`run_preview`) 而非 LLM 模拟进行代码验证
- `simplified-workflow`: LangGraph 工作流从 8 节点精简为 4 节点，删除多角色模拟层
- `tool-output-reform`: 修复 lint_js 误报、write_file 返回元数据、edit_file 提升为默认编辑工具

### Modified Capabilities

（无——openspec/specs/ 当前为空，所有 capability 均为新建）

## Impact

- **Affected code**:
  - `backend/harness/runtime.py` — ToolCallLoop 集成 Hook 检查、write_file 结果格式变更
  - `backend/harness/instructions/file_coder.py` — 集成 CompletionContract
  - `backend/harness/instructions/nodes.py` — 删除 pm/architect/qa/tester/summarize 节点，新增 verify 节点
  - `backend/harness/graph.py` — 简化工作流（8→4 节点）
  - `backend/harness/tools/code_tools.py` — 修复 lint_js ES Module 检测
  - `backend/harness/constraints/` — 新增 completion_contract.py + progress_hooks.py
  - `backend/harness/instructions/prompts/` — 新增 verify prompt，删除多角色 prompt，更新 coder prompt
  - `backend/harness/instructions/prompts/coding/` — file_aware_coder.md 和 coder_ml.md 更新 edit_file 定位
- **Deleted files**: `role_executor.py`, `orchestrator.py`, `simple_coder.py`, `summarize.py`；多角色 prompt 模板（pm/architect/qa）
- **No changes to**: SSE 推送机制、前端、edit_file 实现、API 路由
