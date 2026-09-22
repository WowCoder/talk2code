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
```

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

驱动**真实的 `ToolCallLoop`** 生成代码（不是 mock），因此评估的是端到端真实质量：
Planner → Coder（write_file/edit_file）→ 验证闭环（run_preview）→ 修复。

## 断言类型

| 类型 | 说明 |
|---|---|
| `file_exists` | 指定文件存在 |
| `content_contains` | 文件包含某字符串 |
| `content_not_contains` | 文件不含某字符串（反模式：innerHTML/eval） |
| `html_has_element` | index.html 含某选择器对应元素 |
| `preview_no_error` | Playwright 运行无 pageerror/console.error |
| `file_min_lines` | 文件行数 ≥ N（防空文件偷懒） |

## 何时跑

- 改了 prompt / 工具 / 验证逻辑后，跑一次对比通过率变化
- 发版前跑全量（含 preview）确认无回归
- PR 里附 `--compare` 输出，让质量变化可量化

## 输出

- `results/baseline_<ts>.json` — 完整结果（机器可读）
- `results/baseline_<ts>.md` — 可读摘要 + 明细表
- `results/latest.json` → 最新 json（软链，便于 `--compare ../eval/results/latest.json`）

## 任务集

20 个需求，4 个难度：
- **L1**（5）：单页静态展示 —— 名片/着陆页/文章/定价表/画廊
- **L2**（7）：交互 + localStorage —— 待办/计数器/计算器/颜色选择器/留言板/标签页/番茄钟
- **L3**（5）：复杂多状态 —— 购物车/天气卡/表单验证/排序可视化/搜索过滤
- **L4**（3）：综合 —— 仪表盘/笔记应用/看板

新增任务：编辑 `tasks/tasks.yaml`，每条配 `assertions` 即可，无需改代码。
