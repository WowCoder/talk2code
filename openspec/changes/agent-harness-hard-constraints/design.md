## Context

当前 Agent 工作流依赖 LangGraph 8 节点编排（TeamLeader → PM → Architect → FileByFileCoder → Tester → Summarize → Repair），约束机制仅靠 prompt 文本注入。Requirement #36 暴露了这种软约束的根本缺陷：Agent 无视 19 次系统提醒、陷入 lint 误报循环、反复读取已写入文件。

参考 Anthropic Harness Engineering 博客（2026.03）和 `cwc-long-running-agents` 参考实现的设计原则：

1. **Constraints > Instructions** — Hook 阻断 > Prompt 提示
2. **Separate Generation from Evaluation** — 独立上下文评估 > 同上下文审查
3. **Externalize Memory** — 文件系统状态 > AgentState 序列化
4. **Default-FAIL Contract** — 初始全部失败，需证据才能通过

## Goals / Non-Goals

**Goals:**
- 用 PreToolUse Hook 硬阻断替代 prompt 文本提示，确保 Agent 无法无视系统约束
- 用 Fresh-Context Evaluator（真实浏览器执行）替代 LLM 模拟审查，提供 ground truth 验证
- 简化 LangGraph 工作流，删除对现代模型不再必要的多角色模拟节点
- 修复 lint_js、write_file、read_file 循环等工具层面的具体问题
- 所有目标文件在任务结束前必须创建完成（Completion Gate）

**Non-Goals:**
- 不改变 SSE 推送机制和前端
- 不引入主动上下文重置（仅作为异常恢复的安全网）
- 不重写 edit_file 的 SEARCH/REPLACE 实现（当前实现质量已足够）
- 不修改 LLM 客户端和配置管理
- 不引入真实的单元测试框架（`run_preview` 浏览器执行已覆盖前端验证场景）

## Decisions

### 决策 1: CompletionContract 用独立 JSON 文件而非 AgentState 字段

**选择**: `.task/contract.json` 文件 + PreToolUse Hook 自动更新

**理由**:
- 文件系统状态不受 LangGraph 节点替换影响（AgentState metadata 在节点间可能丢失）
- Hook 可以在不经过 LLM 的情况下确定性更新状态（零 token 消耗）
- 崩溃恢复时，直接读取 contract.json 即可知道进度，不依赖对话历史
- 借鉴 Anthropic 的 `test-results.json` Default-FAIL 模式

**替代方案**: 
- AgentState 内存字典 — 被否决，节点序列化可能丢失
- 纯 LLM 追踪（在 system prompt 中） — 被否决，已被 Requirement #36 证明不可靠

### 决策 2: PreToolUse Hook 阻断时机

**选择**: 阻断 `read_file`（刚写入 2 轮内）+ 阻断 `task_complete`（contract 未全部完成）

**理由**:
- `read_file` 阻断基于事实（文件何时写入），不依赖 LLM 判断
- `task_complete` 阻断基于 contract.json 的确定性状态
- 两个阻断条件都是可验证的客观事实，不会误判

**不阻断的场景**:
- Agent 主动 `read_file` 未刚写入的文件（正常的编辑前阅读）
- Agent 调用 `list_files`（低成本操作，不影响进度）
- Agent 调用 `lint_js`/`lint_css`（修复后将成为可靠工具）

### 决策 3: Fresh-Context Evaluator 的实现方式

**选择**: 独立 LLM 调用 + `run_preview` 浏览器输出 + 只有 Read 工具

**理由**:
- "Models reliably over-praise their own work" — 同一个上下文中的 LLM 会延续生成阶段的假设和盲区
- 真实浏览器 console 错误提供了 LLM 无法伪造的 ground truth
- 只有 Read 工具确保 Evaluator 不能修改代码（纯粹评估角色）

**替代方案**:
- 继续使用 tester_node（LLM 模拟验证 AC） — 被否决，没有 ground truth，盲区与生成阶段重叠
- 纯浏览器验证（不调 LLM） — 部分保留，浏览器输出作为 Evaluator 的输入数据，但仍需要 LLM 做语义判断（如 UI 是否合理）

### 决策 4: 工作流简化的粒度

**选择**: 4 节点（TeamLeader → Coder → Verify → Repair）

**理由**:
- PM / Architect 角色为较弱模型设计的补丁——现代模型（Opus 4.5 / Sonnet 5）可以在单个 TL prompt 中完成需求分析 + 架构设计
- QA / Summarize 两个 LLM 审查节点合并为一个 Fresh-Context Evaluator，提供更强的验证能力
- Repair 节点保留，但触发条件改为 Evaluator 返回 NEEDS_WORK

**删除节点的理由**:
| 节点 | 删除理由 |
|------|---------|
| PM | TL 可直接输出 PRD 摘要（含在 Plan JSON 中） |
| Architect | TL 可直接输出技术选型 + 文件结构 + 接口契约 |
| QA (LLM审查) | 替换为 Fresh-Context Evaluator，提供 ground truth |
| Summarize (LLM审查) | 同上 |
| Tester (LLM模拟AC) | 替换为 Evaluator 的 run_preview 真实执行 |

### 决策 5: write_file 结果格式

**选择**: 只返回元数据（文件名、行数、字符数）

**理由**:
- 返回完整内容 → 超长截断 → "已截断"标记 → Agent 想"补全" → read_file → 再次截断 → 死循环
- 这是 Requirement #36 中 Agent 反复读取 app.js 7 次的根因
- Claude Code 的 Write 工具也仅返回 "Wrote N lines to path" 这样的元数据

**替代方案**:
- 返回完整内容但移除截断标记 — 被否决，超大文件（>8000 字符）仍会撑爆上下文
- 不改变但加强 prompt — 被否决，属于软约束，已被证明无效

### 决策 6: edit_file 定位调整

**选择**: Prompt 中明确 edit_file 为修改已有文件的默认工具，write_file 仅用于创建

**理由**:
- 当前所有 prompt 模板只提到 write_file，Agent 不知道 edit_file 可用
- Claude Code / Aider 的默认编辑模式都是 SEARCH/REPLACE（即 edit_file）
- edit_file 实现质量足够（多块、空白归一、失败回灌），不需要重写

## Risks / Trade-offs

- **[风险] PreToolUse Hook 过度阻断** → Agent 确实需要读取刚写入的文件时被阻断。缓解：迭代计数阈值可配置（默认 2 轮），且 Hook 阻断时给出明确的原因说明，Agent 可在下一轮重试
- **[风险] Evaluator 成本增加** → 每次评估需要额外的 LLM 调用 + 浏览器启动。缓解：只在所有文件编码完成后触发一次（不是每文件触发），且浏览器复用（不每次重启）
- **[风险] 删除 PM/Architect 后 TL Plan 质量下降** → 单一 prompt 可能不如多角色协作详细。缓解：TL prompt 整合 PRD+SPEC 模板的关键部分；后续可根据实际效果调整 TL prompt
- **[风险] 简化工作流后丢失旧行为兼容** → 现有测试依赖旧节点名称。缓解：保留旧节点函数的 import 路径，在 tasks.md 中安排测试更新

## Open Questions

1. Evaluator 的评分阈值：PASS/NEEDS_WORK 的判定标准是否需要人工校准？（Anthropic 博客提到 Evaluator 初始版本通常过于宽松，需要 3-5 轮调校）
2. CompletionContract 是否需要支持"可选文件"？（当前设计所有文件都必须完成，但某些场景下文件可能在编码过程中被判断为不需要）
3. 删除的旧 prompt 模板文件是否需要保留备份？（建议 git 历史已保留，直接删除）
