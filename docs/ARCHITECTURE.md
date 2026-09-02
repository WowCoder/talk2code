# Talk2Code 架构与设计

> 本文档是 README 的纵深补充，展开 Talk2Code 的工程质量体系：验收模型、交付门禁、
> 跨文件契约、Plan DoD、上下文效率、记忆系统与回归闭环。README 只保留概览，细节都在这里。

## 目录

- [代码质量验收系统](#代码质量验收系统)
- [交付门禁](#交付门禁)
- [跨文件 API 契约（导出闭合）](#跨文件-api-契约导出闭合)
- [Plan DoD 校验](#plan-dod-校验)
- [上下文效率优化](#上下文效率优化)
- [记忆系统](#记忆系统)
- [学习闭环](#学习闭环)
- [快速问答](#快速问答)

## 代码质量验收系统

Verify 节点采用 **确定性证据优先 + LLM 增量判断** 的证据等级模型：

**L0 环境契约（写入时刻拦截）**：
- 运行环境硬约束单一事实源 `constraints/environment_contract.py`（禁止 ES Module/CDN、
  存储兜底、入口可见、引用闭合），渲染注入 TL/Coder prompt，程序化检查供 Hook/lint 复用
- PRE_WRITE Hook 零 LLM 成本拦截 `<script type="module">`、外部 CDN、import/export——
  file:// 沙箱必炸的代码在写入瞬间就被打回，不再等浏览器报错

**L3 交互式验收**：
- LLM 将每条验收条件翻译为 Playwright DOM 操作序列（type/click/assert_exists...）
- 在 headless Chromium 中逐条执行，收集 passed/failed/截图
- 全部 AC 通过 + preview 零错误 → **快速通道 PASS**（跳过 LLM 深度评估；
  UI/代码质量诚实标记为未评估，截图落盘 `.task/evaluator/screenshot.png`）
- 结果实时推送到前端 Spec 面板（AC 级别 ✅/❌）

**L2 深度评估**（AC 未全通过时触发）：
- 双视角 LLM 评估（功能正确性 + 代码/UI 质量），5 维度 1-10 分
- 确定性证据定下限：冒烟缺陷/浏览器错误/AC 失败是机器实测事实，LLM 无权推翻为 PASS
- 未通过时缺陷按类别路由：架构类（模块加载/CDN/文件缺失）携带根因卡片回 Coder 重构，
  局部语法类走小上下文定向修复

## 交付门禁

critical 缺陷未清零的需求不再自动放行为 finished_with_issues，而是转
**needs_user_input** 并附差异报告（未达成 AC 清单 + 关键缺陷明细）。
可用 `DELIVERY_GATE_STRICT=false` 关闭。Chat 人工修改路径同样有轻量闸门：
修改后自动跑一次冒烟，引入确定性缺陷则回滚本次修改并告知用户。

## 跨文件 API 契约（导出闭合）

多文件批量生成的最大风险是「A 文件调用了 B 文件没实现的方法」——页面不报错，
按钮静默失效（需求 124 事故：app.js 用了 utils.js 从没定义的 toast/copyText）。
三层确定性防护：

1. **计划期** `tl_analysis.md` 强制 tasks 声明 `exports`（每文件挂载到 window 的
   全局对象+方法清单）；`plan_validator` 校验被依赖的 js 未声明 exports 即打回。
2. **编码期** `build_api_contracts_section(plan)` 把 exports 渲染成「跨文件 API
   契约」注入 coder prompt——coder 只允许调用清单内方法，未声明能力必须在自己
   文件里实现。
3. **验收期** `check_cross_file_contract()`（确定性、零 LLM）解析各 JS 的实际
   导出与全项目引用，比对缺失；未定义的全局对象（如 `Game is not defined`）
   一并拦截。断裂属架构类缺陷，携根因卡片路由回 coder 重构；`classList` 动态
   类名与 CSS 无匹配则发警告。挂在 verify 冒烟 + task_complete 完成校验两道关。

## Plan DoD 校验

TeamLeader 产出的开发计划在进入 Coder 前经过程序化校验
（`constraints/plan_validator.py`）：文件引用闭合、每个任务有 purpose、
每条 AC 的 how_to_verify 含可操作动词、复杂度与文件数一致。
不合格打回 TL 重出最多 1 次；带病放行会记录弱 AC 清单供下游参考。

## 上下文效率优化

- **write_file 返回内容预览**：写入后返回前 80 行 + 尾 10 行，Agent 无需 read_file 验证
- **PRE_TOOL_USE Hook 真阻断**：写入后 2 轮内实际阻止对同一文件的回读
- **批量文件创建**：允许一次创建 2-3 个相关文件，消除"每次一个文件"的串行瓶颈
- **迭代上限文件数驱动**：3 文件 = 9 轮，5 文件 = 13 轮，按需分配不浪费

## 记忆系统

跨会话经验积累 — AI 会在任务前检索相关历史经验辅助编码，任务后自动总结关键模式供后续复用。
正负经验都沉淀：失败任务显式打 failure 标签、提高重要度，检索命中时以 ⚠️ 警示案例呈现，
避免同类缺陷重蹈覆辙。相似记忆定期由 LLM 合并去重。

## 学习闭环

`eval/tasks/tasks.yaml` 里固化的 21 个回归任务，覆盖的都是**历史上真实踩过的坑**，
而不是凑数的样例：

- `t21` 贪吃蛇 —— 曾连续七次失败的失败模式专项
- `ENV-3` —— `file://` 下 ES Module 被 CORS 拦截（需求 #115 的直接死因）
- `ENV-2` —— 无网络沙箱里 CDN 必挂（#110–116 反复踩坑）

改 harness 核心时前后各跑一次基线做对照，用数据判断「这次改动到底更好还是更差」，
而不是靠感觉。

最近一次全量基线（2026-08-31）：20/21 通过，唯一未通过项定位为上游模型端点读超时，
非架构缺陷。完整报告见 `eval/results/baseline_20260831_113741.md`。

标准命令（在 `backend/` 目录下执行）：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
    -u ALL_PROXY -u all_proxy PYTHONPATH=. \
    ../venv/bin/python ../eval/run_eval.py --no-preview
```

- 虚拟环境在**仓库根目录** `venv/`（不是 `backend/venv/`）；
- eval 是独立进程，必须显式清掉系统代理变量，否则会继承代理、导致 LLM 请求 ProxyError（同生产环境 req #134 根因）；
- `--no-preview` 跳过 Playwright 浏览器验收（仅跑 file/结构/内容断言），速度快、无 429 限流风险；
- 完整链路（含浏览器预览）去掉该开关即可，但耗时 30–60 分钟且 agnes 端点有 429 限流风险。
- 预览验证在 headless Chromium 中**真实加载生成页面**，捕获 `pageerror` / `console_error` / `request_failed` 等运行时错误（含跨文件导出未定义导致的崩溃），与静态结构审计互补，构成「静态 + 运行时」双保险质量信号。
- trace 覆盖全流程：编码迭代 / verify 评估 / defect_repair 修复均有 span 可归因
- `logs/llm_traffic.log` 为 JSON Lines 结构化格式，按天轮转保留 7 天

## 快速问答

简单技术问题走快速通道，直接回答不进入编码流水线，节省 Token 和响应时间。
