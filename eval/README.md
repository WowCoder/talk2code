# Eval 基线评估

把"生成质量"从凭感觉迭代提升到数据驱动：固定 21 个需求，跑真实生成管线，
自动检查断言（文件存在/内容/Playwright 运行时无错），输出可对比的基线报告。

## 用法

```bash
cd backend
PYTHONPATH=. python ../eval/run_eval.py                    # 全量（含浏览器验证）
PYTHONPATH=. python ../eval/run_eval.py --no-preview        # 快跑（跳过浏览器，CI 友好）
PYTHONPATH=. python ../eval/run_eval.py --tasks t01 t06     # 只跑指定任务
PYTHONPATH=. python ../eval/run_eval.py --compare ../eval/results/baseline_xxx.json  # 对比历史
PYTHONPATH=. python ../eval/run_eval.py --with-memory --memory-user 200     # 开记忆（A/B 的 B 组）
PYTHONPATH=. python ../eval/run_eval.py --no-plan           # 退回裸 ToolCallLoop（历史口径 A/B）
```

### 口径：默认走真实规划（2026-10-07 修）

本脚本此前**直连 `ToolCallLoop`**，`state["plan"]` 恒为 None，于是三件防线静默失效：

| 失效项 | 后果 |
|---|---|
| 提示词里的 `## 推荐文件结构 / 实现计划` 整段消失 | 模型不知道要建哪些文件 |
| `_pending_plan_files()` 恒返回 `[]` | 每轮「进度检查：还差 N 个文件」一条都没发出（21 题的提示词里出现 **0 次**） |
| 迭代预算退化成 `max(0,3)+3 = 6` 轮 | 与真实工作量脱钩，起步就烧完了 |

实测代价：**8/21 个任务连 `index.html` 都没创建**就被预算耗尽，断言第一条即判死。

现在默认跑**真实生产管线**：`team_leader_node` → `coder_node`。它同时带回三样东西：

1. 真实计划（文件结构 / 实现顺序 / CompletionContract）
2. `metadata["tool_thinking"] = "disabled"`（裸跑会带着思考模式烧完额度）
3. Phase 2 定向补全：批量编码漏建的文件会被逐个补齐

要复现历史口径请显式加 `--no-plan`。报告 JSON 每条结果带
`plan_used` / `plan_files` / `plan_error` / `rounds` / `tool_sequence`，
MD 摘要另有「失败归因」小节，逐轮列出工具序列 —— 失败不需要再翻日志猜。

### 记忆 A/B 对照

- **默认不开记忆**：eval 历史上从未走过生产端的记忆注入路径，跑的就是 memory-off，
  现有基线可直接当 A 组。
- `--with-memory` 复刻生产注入模式：任务开始时 `build_memory_block()` 检索一次，
  缓存后包装 `_build_system_prompt`，并挂 `loop._memory_block` 供 file_coder 复用。
- `--memory-user N` 指定用哪个用户的记忆检索，**选错用户会让 B 组等于白开**：
  - `0`（默认）在库里没有任何记忆；
  - `1` 的通用记忆与 eval 题库语义几乎不相关，会被下面的相关性门禁整体抑制、注入为空；
  - 要跑出有效信号，用**与题库对齐的专用用户**（如 `--memory-user 200`：其记忆的
    `requirement` 与任务文本对齐，相似度足够、门禁可放行）。
- **相关性门禁**：检索后按「绝对阈值 0.25 + 相对阈值 0.5」两层过滤——最相关的一条都
  不够相关就整体不注入，其余只留相似度达到最相关项一半的候选。目的是避免"注入不相关
  记忆比不注入更糟"（曾出现名片页任务被注入贪吃蛇经验）。
- 报告 JSON 每条结果带 `memory_enabled` / `memory_block_chars`（0 = 该任务没检索到
  内容，等于白开），MD 摘要含"记忆注入"行，方便一眼确认 B 组真的注入了东西。

## 评估什么

驱动**真实的生产节点**生成代码（不是 mock），因此评估的是端到端真实质量：
`team_leader_node`（规划）→ `coder_node`（write_file/edit_file + 契约 + 定向补全）。
断言由本脚本在产物上跑，不经过 verify / defect_repair —— 也就是说
本集测的是**交付物本身的正确性**，不是「自评打分」。

## 断言类型

| 类型 | 说明 |
|---|---|
| `file_exists` | 指定文件存在 |
| `content_contains` | 文件包含某字符串 |
| `content_not_contains` | 文件不含某字符串（反模式：innerHTML/eval） |
| `html_has_element` | index.html 含某选择器对应元素（支持标签名 / `.class` / `#id` / `[class*=x]` / **逗号并集**） |
| `preview_no_error` | Playwright 运行无 pageerror/console.error |
| `file_min_lines` | 文件行数 ≥ N（防空文件偷懒） |

### 断言必须容得下「同等合法的另一种写法」

断言写得太字面，测的就不是产物质量，而是**模型有没有猜中我们心里的那个写法**。
2026-10-07 全量跑实测到三处同类假阴性，全部按「放宽到合法写法的并集」修：

| 断言 | 被误杀的写法 | 修法 |
|---|---|---|
| `html_has_element: button`（t02 着陆页 CTA） | `<a class="btn" href="#cta">` —— 着陆页 CTA 更常见、语义也更对（跳转不是提交） | 选择器改为并集 `button, [class*=btn], [class*=button]`，匹配器支持逗号并集与 `[class*=x]` |
| `content_contains: style.css`（t01 名片页） | 单文件方案把样式写在 inline `<style>` 里 | 查 `.css` 时把 HTML 的 `<style>` 块一并纳入；`content_not_contains` 同样覆盖 |
| `file_min_lines min: 20`（t01） | 「标题 + 职位 + 简介」的写实名片页只有 17 行 | 降到 12（仍远高于任何真实空壳的 < 5 行） |

判断标准：**一个正常的前端工程师会写出这种写法吗？** 会，就是断言的问题，不是产物的问题。

## 何时跑

- 改了 prompt / 工具 / 验证逻辑后，跑一次对比通过率变化
- 发版前跑全量（含 preview）确认无回归
- PR 里附 `--compare` 输出，让质量变化可量化

## 输出

- `results/baseline_<ts>.json` — 完整结果（机器可读）
- `results/baseline_<ts>.md` — 可读摘要 + 明细表
- `results/latest.json` → 最新 json（软链，便于 `--compare ../eval/results/latest.json`）

## 任务集

21 个需求，4 个难度：
- **L1**（5）：单页静态展示 —— 名片/着陆页/文章/定价表/画廊
- **L2**（7）：交互 + localStorage —— 待办/计数器/计算器/颜色选择器/留言板/标签页/番茄钟
- **L3**（6）：复杂多状态 + 回归专项 —— 购物车/天气卡/表单验证/排序可视化/搜索过滤/贪吃蛇
- **L4**（3）：综合 —— 仪表盘/笔记应用/看板

新增任务：编辑 `tasks/tasks.yaml`，每条配 `assertions` 即可，无需改代码。
