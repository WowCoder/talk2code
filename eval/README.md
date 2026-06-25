# Eval 基线评估

把"生成质量"从凭感觉迭代提升到数据驱动：固定 20 个需求，跑真实生成管线，
自动检查断言（文件存在/内容/Playwright 运行时无错），输出可对比的基线报告。

## 用法

```bash
cd backend
PYTHONPATH=. python ../eval/run_eval.py                    # 全量（含浏览器验证）
PYTHONPATH=. python ../eval/run_eval.py --no-preview        # 快跑（跳过浏览器，CI 友好）
PYTHONPATH=. python ../eval/run_eval.py --tasks t01 t06     # 只跑指定任务
PYTHONPATH=. python ../eval/run_eval.py --compare ../eval/results/baseline_xxx.json  # 对比历史
```

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
