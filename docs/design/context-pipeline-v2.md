# 短期记忆 v2：上下文管线（Context Pipeline）

> **本文档是最终态设计说明**：只描述改造完成后的运行流程与设计思路，用于后续维护与备份。
> 只保留结论态内容：演进过程、离线探针脚本、环境相关的本地细节均不在此文档内。
> 涉及代码：`harness/state/context_pipeline.py`、`harness/runtime.py`、`harness/tools/`、`harness/observability/`。

---

## 1. 一句话方案

放弃「事后压缩对话」，改为**按信息的可重建性分类治理**：

- **可重建**的（文件内容、工具输出）→ **允许**遮蔽；需要时重新调工具拿回来，零信息损失。
- **不可重建**的（需求、决策与理由、失败、对话）→ **永不丢弃**，全量保留。

Agent 自己维护结构化状态文件；LLM 摘要**只在极端任务上兜底**。

---

## 2. 设计原则（五条，冲突时按序裁决）

1. **按可重建性分类，不按位置**：文件内容可随时用 `read_file` 重建 → 允许遮蔽；决策、失败原因、对话不可重建 → 永不丢弃。
   分类只决定「谁**可以**被遮蔽」，**何时遮、遮多少**完全由 token 预算决定。
   **绝不按文件名或内容语义去重** —— 实测会删掉真信息（见 §7）。
2. **状态由 Agent 写，harness 不做语义理解**：`TASK_STATE.md` 是 Agent 的第一职责；harness 只检测「是否过期」，从不解析内容、从不代写。
3. **前缀稳定**：系统提示的稳定前缀从第 2 轮起字节级不变 → 命中 KV-cache，降本降延迟。
4. **Compaction 是兜底不是日常**：只有前三层都装不下时才触发；此时被摘要的只剩高密度对话，而不是文件内容。
5. **一切削减可观测**：每轮记录遮蔽/丢弃/压缩的条数与 token，可事后审计。

---

## 3. 架构：六层管线（L0–L5）

```
L0 上游修复     dialogue_history 所有 role 有明确通道；preserve 标记透传
      │
L1 稳定前缀     稳定前缀 = 模板骨架 + 需求 + 计划摘要 + 接口契约（字节稳定 → KV-cache 命中）
      │         可变尾段 = 工作区文件索引 + TASK_STATE.md（每轮重建，挂提示词末尾）
      │
L2 任务状态     .task/TASK_STATE.md：Agent 通过 update_task_notes 维护
      │          （目标 / 决策与理由 / 文件状态 / 未决问题 / 下一步）
      │
L3 工具结果     [3a 入口闸门] 单条结果 > SINGLE_RESULT_LIMIT → 截断 + 行号重读提示（每条都执行）
   生命周期      [3b 存量遮蔽] 预算驱动、按需执行：装得下 → 一条都不遮蔽；
      │                       装不下 → 按「失效优先，其次时间序从最旧」遮蔽 read_file；
      │                       占位符带一句话摘要；非文件大结果先落盘 .task/refs/；
      │                       最近 KEEP_RECENT 条 read_file 永不遮蔽；
      │                       write/edit 只留变更摘要行（不回显整份文件）
      │
L4 全历史       不再有固定条数硬上限；3b 已保证尽量装下
   预算装载       装得下 → 全量装载（含全部对话，零消息丢弃）
      │
L5 Compaction   仅当 L4 装不下：最旧段（已遮蔽，只剩对话）交 LLM 摘要 → 注入为一条 summary
   兜底           失败兜底：已遮蔽尾部硬截断 + WARNING
```

**每层职责单一**：L1 管稳定，L2 管语义状态，L3 管体积，L4 管取舍，L5 管极端情况。

---

## 4. 单轮生命周期（一次 LLM 调用的完整流程）

### A. 系统提示组装

```
[稳定前缀：第 2 轮起字节不变，命中 KV-cache]
head = 模板骨架
     + ## 需求            需求原文                       ← 固定
     + ## 实现计划（摘要） _compact_plan_text(plan)        ← 固定（合同，只读）
     + ## 接口契约         build_api_contracts_section     ← 固定
[稳定前缀结束；以下为每轮可变尾段]
     + ## 工作区文件索引   每文件一行「文件名 + 一行结构摘要」（不含正文）  ← 每轮重建
     + ## 任务状态         TASK_STATE.md 全文                          ← Agent 上轮写的
```

三条硬约束：

1. **索引不能退化成「只有文件名」**。结构摘要（HTML title / 元素 id、CSS 选择器、JS 函数名与 DOM 引用）由
   `_build_one_line_file_summary()` 规则提取（纯正则，不调 LLM），它是 Agent 的「文件地图」，决定它要不要去 `read_file`。
   摘要丢失 → 上下文里既无正文也无结构线索，会诱发「刚写完就回读」。
2. **可变尾段必须挂在提示词末尾**（`coder_base.md` 模板末尾的注入槽），不能夹在模板中段——
   否则会把稳定前缀劈成两半，KV-cache 命中率下降。
3. **双文件分工**：实现计划（spec）在 head 里只读注入**摘要**，属稳定前缀，是「合同」，永不改写；
   run 开始时把计划写进 `TASK_STATE.md` 作为种子，之后进度只更新 TASK_STATE.md。

### B. 历史装载（`ContextPipeline.build()`）

```
1) 取 state.dialogue_history 全量（源头永不截断）
2) 角色通道：
     thinking        → 丢弃（推理过程不入上下文）
     tool_call       → user 角色 + "[工具 X 返回结果]" 前缀
     user/agent/assistant → 原样
     system(hidden)  → user 角色 + "[系统提示]" 前缀
     iteration_batch → 不发送（UI 元数据）
3) 工具结果生命周期（L3，两个正交机制）：

   [3a 入口闸门] 任何工具结果在进入历史之前，若超过 SINGLE_RESULT_LIMIT = 2,000 token：
                 截断 + 注明「已截断（共 N 行），如需后续内容请用行号范围重读」
                 read_file 支持 start_line/end_line，返回头带 "(行 X-Y / 共 N 行)"
                 → 截断永远可精确恢复，零信息损失。

   [3b 存量遮蔽] 预算驱动、按需执行（budget 默认 24,000 token）：
                 if est(head) + est(历史，3a 之后) ≤ budget:
                     一条都不遮蔽
                 else:
                     把 read_file 结果换成占位符直到装得下，顺序为：
                       ① 已失效的 read 优先（其目标文件随后被 write/edit 改过 → 内容已过期，
                          留着会诱导模型拿旧文本做 edit 的 SEARCH 匹配）
                       ② 其余按时间序从最旧
                     占位符示例：
                       "[工具 read_file 返回结果]（已省略；文件：js/game.js 行 80-162；
                        摘要：snake 渲染——导出 move/render/collision；
                        如需请用 start_line/end_line 重新读取）"
                     失效项的占位符注明「已被后续 {工具} 修改而过期」。
                   - 摘要由规则提取（首行 / 导出符号 / 关键字段，纯正则或 AST，不调 LLM，确定性零成本）
                   - 最近 KEEP_RECENT = 3 条 read_file 永不遮蔽
                   - 遮蔽到满足预算后再多留一小段余量（headroom），避免下一轮立刻又触发、反复打断 KV-cache
                   - 非文件类大结果（command / search / API）在替换前**先全文落盘**
                     `.task/refs/<id>.md`，占位符带路径与一句话摘要 → 文件不再是唯一可重读源
                   - 永不遮蔽：write/edit 变更摘要行、验证结果、系统提示
                     （write/edit 的完整文件回显仍只留首行变更摘要）
4) L4 全量装载：3b 已保证尽量装下 → 装得下就全量装载（含全部对话，零消息丢弃）
5) L5 兜底：装不下 → 最旧段交 LLM 摘要为一条 ≤500 token 的 summary；
   LLM 失败 → 已遮蔽尾部硬截断 + WARNING
```

**为什么不做「按文件去重」**：实测 31% 的任务存在同一文件被**分段读**（不同行号区间）的情况，
按文件去重会把不同区间的旧读当成「重复」干掉——那是删真信息。主流实现
（Anthropic `clear_tool_uses`、Claude Code、Codex CLI、OpenHands）**一律是时间序 + 预算**，不做语义判断。

### C. 回填与 staleness 提醒

```
1) 新消息 append 进 dialogue_history（源头永不截断）
2) Agent 通过 update_task_notes 维护 TASK_STATE.md
3) harness 检测（唯一介入点）：累计未记录的文件变更次数 ≥ STALENESS_THRESHOLD = 2
   → 下一轮注入一条 user 提醒（只提醒，不代写）
```

### D. 可观测（每轮一条结构化日志）

```
[ContextPipeline] head=5.4k history=3.4k/98msg masked_read=12
                  dropped=0 compacted=0 cache_hit=true
```

### E. 交付边界与跨轮次（run 边界的折叠）

前几节只管「单次 run 内」的窗口管理。用户验收交付后**追加需求 / 继续对话**时，需要一条 run 边界规则，
否则上一轮几千条工具调用会原样带进下一轮，撑爆窗口且充满噪音。

1. **交付 = checkpoint**：Agent 把 TASK_STATE.md 收束成 **handoff 摘要**，写入独立文件
   `.task/DELIVERY.md`（不污染 TASK_STATE.md）：做了什么 / 关键决策与理由 / 已知问题 / 怎么跑怎么验 / 原始需求快照。
2. **工具轨迹归档，不是删除**：上一轮的 read/edit/verify 结果**不再带入下一轮上下文**（它们 100% 可重建），
   但必须**先落盘归档到 `.task/EXECUTION.jsonl` 再移除**：
   「不带入上下文」是上下文管理目标；「不留痕」不是。归档件属 dev artifact，不进 prompt。
3. **状态折叠带出**：handoff / TASK_STATE.md 作为下一轮的起点状态带入（它是不可重建的记忆）。
4. **下一轮初始上下文** = [新需求消息] + [稳定前缀] + [文件索引] + [TASK_STATE.md / handoff]
   + [workspace 实际文件，按需 just-in-time 读]；上一轮工具轨迹不出现。
5. **不可重建项必须跨边界保留**：需求本身、决策与理由、交付结果、用户反馈，
   只活在 TASK_STATE.md / handoff 与 requirement 状态里，不活在工具轨迹里。

每个 requirement 一个 `dialogue_history`，交付即 checkpoint：同 requirement 延续走同一 history 但触发一次折叠；
新 requirement 则新 history 从 handoff 起种子。

### F. 长耗时调用的可见性与失败可观测

管线只管「给 LLM 看什么」，但一次 run 里还有非管线环节会让长耗时看起来像卡住。三方各司其职：

1. **节点层超时**：缺陷修复节点的 LLM 调用超时读 `settings.DEFECT_REPAIR_TIMEOUT`（范围 10–300，`.env` 可覆盖），
   **不硬编码**。端点变慢时更快失败，同时保留按环境调参的口子。
   注意：**单次超时 ≠ 总时长** —— 客户端还会按 `LLM_MAX_RETRIES` 重试，用户感知的是「超时 × 尝试次数」。
2. **路由层心跳**：SSE 必须用**命名事件**（`event: heartbeat`，带 `elapsed_s`），
   不能用 SSE 注释行（`: heartbeat`）——注释行浏览器不会交给业务代码，看着在保活、前端实则零感知。
   前端据此显示「工作中 · 已等待 N 分 N 秒」，把「真卡住」和「只是慢」区分开。
3. **日志层埋点**：Agent 执行明细日志必须覆盖**失败路径**（缺陷修复、验证评估），
   不能只覆盖主循环——恰恰是最容易出问题的修复/校验环节最需要记录。

---

## 5. TASK_STATE.md 机制（L2 的核心）

**由 Agent 写，不由 harness 抽**——这是与「规则状态卡」的本质区别。

```markdown
# .task/TASK_STATE.md
## 目标
做一个贪吃蛇小游戏，有排行榜、可分享、Q 版画风
## 决策与理由
- 渲染用 Canvas 而非 DOM：动画帧率要求
- 分数存 localStorage：需要离线可用
## 文件状态
- js/game.js    完成（412 行）  待修：180° 掉头 bug
- css/style.css 已重写（478 行）
## 未决问题
- share.js 在 Safari 下 share API 不可用，降级方案未定
## 下一步
修 game.js 掉头 bug → 跑 run_preview → 提交验证
```

**三个保障机制**（让它真的被维护）：

1. **系统提示明确职责**：模板里写明「你是 TASK_STATE.md 的维护者」。
2. **专用工具 `update_task_notes`**：比 `write_file` 更轻——按小节更新，harness 只校验小节名合法，**不解析语义**。
3. **staleness 检测**：累计 2 次文件变更而 notes 未更新 → 注入一条 user 提醒。
   仍不写也只影响质量，不影响正确性（正确性由 L4 全历史保留保障）。

**为什么它取代了摘要的大部分价值**：compaction 需要猜测什么重要；TASK_STATE.md 是 Agent 在**信息产生的那一刻**
自己判断并写下的，不存在「事后回忆漏掉」的问题。

> 备注：任务状态保持**纯文本小节**，不转 Mermaid 流程图——流程线性、无并发/分支依赖，纯文本已足够表达。

---

## 6. 替换关系（老 → 新）

| 原先做法 | 新方案 |
|---|---|
| 对话条数硬截断（`relevant[-N:]`） | **全历史预算装载**（L4） |
| `ContextCompactor`（多层压缩器） | **整体退役** → `ContextPipeline`（L3/L4/L5） |
| head 含多行文件摘要 | **一行索引** + just-in-time `read_file` |
| `role="system"` 的历史提示被丢弃 | **L0 角色通道修复**（带 `[系统提示]` 前缀进入消息列表） |
| `preserve` 标记透传断裂 | **L0 一并修复** |
| 无任务状态载体 | **TASK_STATE.md**（Agent 自写） |
| 每轮重发全量前缀 | **稳定前缀字节稳定**（KV-cache） |
| 削减不可审计 | **每轮结构化日志** |

---

## 7. 设计依据（离线测量结论）

口径：模拟本方案 L3（3a 入口截断 + write/edit 只留首行摘要），作用对象为**全部历史**，
样本为生产库真实任务 121 条。

| read_file 策略 | 中位 token | 均值 token | ≤ 预算占比 |
|---|---|---|---|
| **R0 完全不遮蔽**（基线） | 5,057 | 10,784 | **84%** |
| R1 保留最近 3 条 | 3,404 | 4,044 | 98% |
| R2 每文件保留最近一次（**已证伪**） | 1,543 | 3,213 | 98% |
| **R3 时间序 + 预算驱动（采用）** | **5,057** | **6,827** | **98%** |

- **R3 的中位数与 R0 相同** —— 因为它只在超预算时才动手。
  即 **84% 的任务完全不需要遮蔽**；真正需要遮蔽的只有 **16%**；按需遮蔽后装不下的仅 **2%**（走 L5）。
- 窗口构成（按 token 占比）：`read_file` 结果约 63%（可重建），`write_file`/`edit_file` 回显约 24%（可截），
  其余为需求/计划/对话等不可重建内容 → **主攻方向选对了**。

**R2 为什么被证伪**：31% 的任务存在分段读；R2 规则下会遮蔽 297 次旧读，
其中 **249 次（84%）的区间与被保留读不同** = 真丢信息。即 R2 的假设「同文件多次读 = 重复读」在真实数据里只有 16% 成立。

**业界同构**：主流 Agent 工具链中工具输出同样占 token 大头（约 79%），
印证「工具结果是 token 瓶颈」是行业共识。

**每层的生产先例**（本方案不照抄任何单一产品，但每层做法均有生产验证）：

| 层 | 做法 | 先例 |
|---|---|---|
| L1 稳定前缀 | head 字节稳定 + KV-cache 优先 | Manus（前缀完全稳定，连时间戳都不放）、Codex CLI（prefix 严格保持，工具枚举顺序不一致会导致 cache miss） |
| L2 状态文件 | Agent 自写 todo/状态并复述到上下文尾部 | Manus（`todo.md` 每步改一行，对抗 lost-in-the-middle）、Claude Code（to-do 工具） |
| L3a 入口闸门 | 工具结果**截断后**才进历史 | Codex CLI `tool_output_token_limit`、Anthropic `str_replace_based_edit_tool` 的 `max_characters` |
| L3b 存量遮蔽 | 超预算按时间序遮蔽最旧 + 占位符 | Anthropic `clear_tool_uses`（`keep` 默认 3、`clear_at_least` 防抖、`exclude_tools` 白名单）、Claude Code、JetBrains 论文（遮蔽 ≈ LLM 摘要） |
| L4 全历史保留 | 可重建内容遮蔽后全留；错误留在现场 | Manus（文件系统当无限外脑；错误与失败必须留在上下文）、OpenHands（Event Store：append-only + 抑制标记而非删除） |
| L5 摘要兜底 | 仅超预算时摘要 | Claude Code / Codex CLI / Gemini CLI / Cursor / Copilot / Cline 等普遍采用 |
| 文件按需读 | 索引进上下文、正文按需读 | Manus（存路径而非全文）、Codex / Claude Code |

**占位符摘要与非文件结果落盘的出处**：借鉴腾讯云 *TencentDB Agent Memory* 的「上下文卸载」思路
（其 L1 工具级摘要 / L0 原始证据落盘），在本地简化为**规则提取的一句话摘要** + `.task/refs/` 原文落盘，
只采纳机制、不引入其多级物理存储——workspace 文件本身已经是天然的一级存储。

**超出通用做法的两点**（自有实测/设计）：
1. **L4 全历史装载**：主流用「固定窗口 + compaction」；实测 84% 的任务不做任何遮蔽即可全历史装载，
   按需遮蔽后达 98%。这是「文件系统外脑」逻辑推到极致的自然结果。
2. **L2 的 staleness 检测**：主流靠 prompt 约定让 Agent 更新状态文件；这里额外加了 harness 侧检测作为工程保障。

---

## 8. 关键常量与文件布局

| 常量 / 路径 | 值 / 说明 |
|---|---|
| `budget` | 24,000 token（装载预算） |
| `SINGLE_RESULT_LIMIT` | 2,000 token（单条工具结果入口上限） |
| `KEEP_RECENT` | 3（最近 N 条 read_file 永不遮蔽） |
| `KEEP_TAIL` | 6（尾部保留条数下限） |
| `STALENESS_THRESHOLD` | 2（累计未记录的文件变更次数） |
| `.task/TASK_STATE.md` | Agent 维护的易变工作笔记 |
| `.task/DELIVERY.md` | 交付 handoff 摘要 |
| `.task/EXECUTION.jsonl` | 交付折叠时归档的工具轨迹（每行一条 JSON） |
| `.task/refs/<id>.md` | 被遮蔽的非文件类工具结果原文落盘 |
| `.task/ac_scripts.json` | AC 脚本缓存（hash 含渲染方式签名，见下） |
| `AGENT_EXEC_LOG` / `AGENT_EXEC_LOG_DIR` | Agent 执行明细日志开关与目录（默认 `logs/agent_exec`），输出 `<req_id>.jsonl` |
| `DEFECT_REPAIR_TIMEOUT` | 缺陷修复节点 LLM 超时（秒，10–300） |

---

## 9. 测试矩阵（设计约束，实现须满足）

管线断言：

| 编号 | 断言 |
|---|---|
| P1 | `role="system"` 的历史提示进入消息列表（带 `[系统提示]` 前缀） |
| P2 | `preserve=True` 标记透传到 pipeline |
| P3 | **预算内零遮蔽**：`est(head)+est(历史) ≤ 预算` → read_file 原文一条不改 |
| P4 | 超预算 → 按「**失效优先**，其次时间序从最旧」遮蔽 read_file；失效项注明过期原因 |
| P4a | **最近 3 条 read_file 永不遮蔽**（keep 下限），即使遮蔽完仍超预算 |
| P4b | write/edit 摘要行、验证结果、系统提示**永不遮蔽**（exclude 白名单） |
| P4c | 同一文件分多段（不同行号区间）读，预算内各段全部保留 |
| P4d | **失效 read 优先遮蔽**：某文件的 read 之后该文件又被 write/edit 改动 → 该 read 优先被遮蔽，占位符注明「已被后续 {工具} 修改而过期」；仍有效的历史 read 保留 |
| P4e | 被遮蔽的 read_file 占位符含「一句话规则摘要」，不空泛、可定位 |
| P5 | write/edit 结果只留首行变更摘要 |
| P5a | 单条工具结果超 2,000 token → 截断 + 重读提示（含单行巨型文件按字符截断） |
| P5b | 非文件类大工具结果被遮蔽前全文落盘 `.task/refs/<id>.md`，占位符带路径 + 摘要；原文不丢 |
| P6 | 全历史在预算内 → **零消息丢弃** |
| P7 | 超预算 → 最旧段进 LLM 摘要，摘要进入输出 |
| P8 | LLM 摘要抛异常 → 回退硬截断，不抛出 |
| P9 | `update_task_notes` 注册且可调用；notes 内容进入 head |
| P10 | 累计 2 次文件变更而 notes 未更新 → 出现提醒 |
| P11 | 同一 state 第 2、3 轮的**稳定前缀字节相同**（缓存稳定） |
| P11a | 文件索引为每文件一行「文件名 + 一行结构摘要」，含结构线索且不含正文 |
| P12 | 每轮输出结构化日志（masked/dropped/compacted 计数） |
| P13 | 空历史 / 全工具消息 / 巨型单消息不崩 |
| P14 | 交付 checkpoint：上一轮工具轨迹不带入下一轮；`DELIVERY.md` 作起点状态注入；不可重建项跨边界保留 |
| P14a | **轨迹归档不丢失**：交付折叠时 thinking/tool_call/system 写入 `.task/EXECUTION.jsonl`，`dialogue_history` 只保留人类对话 |

预览断言语义（与本次改动同批落地，防止「假红/假绿」）：

| 编号 | 断言 |
|---|---|
| V1 | 页面**确无 canvas** 时，canvas 断言记为「不适用」，且**计入驱动失败** → 整体不通过（**未验证 ≠ 通过**） |
| V2 | 页面**有** canvas 但快照无变化 → 判失败（真红） |
| V3 | 「requestAnimationFrame 未被调用」警告**仅在确检测到 canvas 时**输出；无 canvas 的回合制游戏不产生该警告 |

---

## 10. 风险与回滚

| 风险 | 缓解 |
|---|---|
| write/edit 去掉全文件回显后 Agent 行为变化（可能更多 read_file） | 重读成本为净收益（业界实测削减 67%）；A/B 验证通过率不降 |
| Agent 不维护 TASK_STATE.md | staleness 提醒；它只是质量增强，正确性由 L4 全历史保障 |
| 全历史装载让单轮 prompt 变大 | 实测更小；且前缀缓存命中后边际成本低 |
| L5 LLM 摘要引入非确定性 | 只对极端任务生效；失败有硬截断兜底 |
| 遮蔽会改变前缀 → 打断 KV-cache | 3b **只在超预算时触发**；一次遮蔽留 headroom 减少触发频次（`clear_at_least` 的防抖思路） |
| 极端任务遮蔽完仍装不下 | 走 L5 摘要；再失败才硬截断 + WARNING |
| 占位符摘要规则覆盖不全 | 摘要仅作辅助提示；文件名+行号 / refs 路径都在，重读永远可恢复原文 |
| 交付 handoff 摘要遗漏关键决策 | 非重建项同时落在 requirement 状态 + DELIVERY.md；handoff 只影响效率不影响正确性 |
| 改动面大 | 隔离分支 + TDD 全覆盖；单 commit 可 revert |
| `.task/refs/` 带来磁盘增长 | 纳入 workspace 生命周期清理；单任务 refs 体积可控（仅被遮蔽结果） |
| 长耗时环节让用户误判「卡住」 | 三方兜底：节点层超时可配置 + 路由层 SSE 心跳事件 + 日志层失败路径埋点（§4.F） |

**回滚**：`git revert` 整个分支合并 commit；`dialogue_history` 源头从未被截断，回滚无数据损失。
