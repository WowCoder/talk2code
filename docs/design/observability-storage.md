# Agent 可观测性 —— 存储与实现 Spec

> 目标：让运营后台（仅管理员可见）能按需求查看完整 Agent 流程（阶段、LLM 调用、参数与返回值），支持多轮对话维度的回溯，且每一类事件都能直接回答一个排查问题。
> 本文只描述**该做什么、为什么这么做、验收标准**，不包含实现细节之外的运维信息。

---

## 1. 背景与问题

当前一次需求执行产生三类日志，各自为政、互不可查：

| 数据源 | 内容 | 问题 |
|---|---|---|
| `agent_exec/<req>.jsonl` | ToolCallLoop 的 `llm_turn` / `tool_call` | 只覆盖 Coder 工具循环，不覆盖 TeamLeader / Verify / DefectRepair |
| `llm_traffic.log` | 按 `call_id` 串联的 request/response | 纯文本追加，无索引 |
| `agent_traces` 表 | 整条 trace 的 spans/tokens/cost | 早期版本只在 `end_trace()` 一次性落库 |

三个直接后果：

1. **覆盖率低** —— 已完成需求中仅约 1/4 有 trace，进行中 / 中断 / 崩溃的完全没有
2. **无法穿透定位** —— `trace_id`、`iteration`、`call_id` 三者互不相通，点开某次 LLM 调用看参数做不到
3. **体量不可控** —— 全量原始日志均值 859 KB/需求

可见性边界：**Agent 轨迹是运维排障工具，仅管理员可见**（全部接口 `@admin_required`），不做按用户隔离，也不向终端用户暴露。

---

## 2. 实测数据（方案地基）

对 49 个已完成需求的日志做全量测算：

| 指标 | 数值 |
|---|---|
| 原始日志合计 | 41.1 MB，均值 **859 KB**/需求，中位 749 KB |
| content 寻址后合计 | 12.8 MB，均值 **268 KB**/需求，中位 219 KB |
| **整体压缩比** | **3.21x**（区间 1.29x ~ 6.12x） |
| 1000 需求外推 | 262 MB |
| 10000 需求外推 | 2.6 GB |

### 2.1 为什么必须 content 寻址，而不是差分

「只存本轮新增 message」的 add-only 差分压缩比看起来更优（实测 request 部分 4.7x），但**存在正确性 bug**。上下文管线有三重机制会改写 `messages[]`，差分无法表达"替换"与"删除"：

| 机制 | 对 messages 的影响 |
|---|---|
| L3b 存量遮蔽 | 历史 `read_file` 结果 → 占位符（**替换**） |
| L5 Compaction | 最旧段 → 一条 LLM summary（**替换 + 删除**） |
| 交付边界折叠 | 追加需求时工具轨迹整体不带入（**大幅重建**） |

实测样本中已观察到「某轮有 1 条 message 从原文被替换为占位符」。add-only 差分会把占位符当新增存入，还原时**原文与占位符版本同时存在**，重构出的 request 与实际发给 LLM 的不一致。

> 教训：差异体现在测试数据非单调递增，且通常只在特定条件下出现。
> 任何基于差分的存储方案，都必须先用**触发过改写的样本**验证还原正确性，不能用未触发样本下结论。

### 2.2 content 寻址方案验证

方案：每条 message 的 content 按 hash 去重存储，每轮存一个有序的 `(hash, role)` 列表。

对 7 个样本（含遮蔽触发频次最高的若干需求，单次最高 26 次遮蔽）验证：

| 样本 | 轮次 | 原始 | 压缩后 | 压缩比 | message 总数 | 唯一内容 | 复用率 | 还原 |
|---|---|---|---|---|---|---|---|---|
| A | 10 | 460 KB | 96 KB | 4.8x | 185 | 33 | 82% | ✅ |
| B | 10 | 778 KB | 171 KB | 4.6x | 435 | 68 | 84% | ✅ |
| C | 9 | 636 KB | 149 KB | 4.3x | 407 | 60 | 85% | ✅ |
| D | 9 | 627 KB | 127 KB | 4.9x | 307 | 45 | 85% | ✅ |
| E | 8 | 608 KB | 106 KB | 5.8x | 234 | 39 | 83% | ✅ |
| F（重度） | 9 | 637 KB | 162 KB | **3.9x** | 820 | 62 | 92% | ✅ |
| G | 8 | 537 KB | 101 KB | 5.3x | 226 | 41 | 82% | ✅ |

**7/7 逐字节精确还原通过**。request 部分整体压缩 **4.70x**，最差样本 3.9x。

### 2.3 跨需求去重：不做

50 个需求中，49 个的 head content 互不相同，仅 1 组（2 个需求）完全相同。

原因：head content 内嵌需求原文，天然各不相同。**跨需求收益 ≈ 0，维持按 `requirement_id` 隔离**（也避免了用户数据跨需求泄露的风险）。

---

## 3. 存储方案：分层

不引入文件系统作为查询介质。核心动作是「按需求筛、按时间排、点开定位到某次调用」，这些全是关系型查询。

| 层 | 内容 | 介质 | 何时加载 |
|---|---|---|---|
| **L1 事件索引** | 一行一事件，轻量可筛选 | PG 表 | 列表页 + 时间线，常驻 |
| **L2 明细** | request / response / tool content | PG JSONB（content 寻址） | 点开某事件才加载 |
| **L3 原文** | 原始 jsonl | 文件（保持现状） | 冷回溯，可选 |

---

## 4. 表结构

两张新表 + 一张 blob 表。**不动任何现有表**，风险最低。

```sql
-- L2-a：message 内容寻址去重表
CREATE TABLE trace_message_blobs (
  requirement_id INTEGER NOT NULL,
  content_hash   VARCHAR(16) NOT NULL,
  role           VARCHAR(16),
  content        TEXT NOT NULL,
  char_len       INTEGER,
  msg_json       JSONB,             -- role/content 之外的整条字段（name/tool_calls/tool_call_id）
  created_at     TIMESTAMP DEFAULT now(),
  PRIMARY KEY (requirement_id, content_hash)
);
CREATE INDEX ix_tmb_req ON trace_message_blobs(requirement_id);

-- L1：事件索引，一行一事件
CREATE TABLE agent_events (
  id             BIGSERIAL PRIMARY KEY,
  requirement_id INTEGER NOT NULL,
  trace_id       VARCHAR(32),
  call_id        VARCHAR(32),
  seq            INTEGER NOT NULL,        -- 需求内全局递增，时间线排序依据
  turn_index     INTEGER DEFAULT 0,       -- 第几轮对话（「轮次切换器」读这个）
  iteration      INTEGER,                 -- 轮内 ToolLoop 迭代
  ts             TIMESTAMP NOT NULL,
  kind           VARCHAR(32) NOT NULL,    -- 见 §6 事件契约
  stage          VARCHAR(32),             -- planning/coding/verifying/repairing/delivering
  label          VARCHAR(200),            -- 时间线文案，如「记忆匹配 · 5 条」
  status         VARCHAR(16),             -- ok/error/blocked
  model          VARCHAR(64),
  duration_ms    INTEGER,
  tokens_in      INTEGER DEFAULT 0,
  tokens_out     INTEGER DEFAULT 0,
  cost           NUMERIC(10,6) DEFAULT 0,
  message_refs   JSONB,                   -- [[content_hash, role], ...] 有序索引
  tools_ref      VARCHAR(16),             -- tools schema 的 content_hash
  payload_id     BIGINT,
  meta           JSONB,
  created_at     TIMESTAMP DEFAULT now()
);
CREATE UNIQUE INDEX ux_ae_req_seq ON agent_events(requirement_id, seq);
CREATE INDEX ix_ae_req_turn ON agent_events(requirement_id, turn_index, seq);
CREATE INDEX ix_ae_kind ON agent_events(kind);
CREATE INDEX ix_ae_ts ON agent_events(ts DESC);
```

```sql
-- L2-b：大字段存放（TOAST 自动移出主表，不拖慢 L1 查询）
CREATE TABLE agent_payloads (
  id             BIGSERIAL PRIMARY KEY,
  requirement_id INTEGER NOT NULL,
  kind           VARCHAR(32),
  tools_ref      VARCHAR(16),   -- tools schema 去重
  response       JSONB,
  tool_content   TEXT,
  raw_path       TEXT,          -- L3 原文路径
  raw_offset     BIGINT,        -- 文件内偏移，冷回溯用
  created_at     TIMESTAMP DEFAULT now()
);
CREATE INDEX ix_ap_req ON agent_payloads(requirement_id);
```

### 4.1 关键工程约束

| # | 约束 | 原因 |
|---|---|---|
| 1 | **新表显式用 `JSONB`** | 现有模型的 `JSON` 泛型在 PG 上映射成 `json`，不支持 GIN 索引与路径查询。模型层用 `JSON().with_variant(JSONB(), "postgresql")`，SQLite 自动降级，不按 `settings.IS_POSTGRES` 手写两套分支 |
| 2 | **`(requirement_id, seq)` 唯一索引** | seq 分配是「读 MAX + 1」，并发写（chat 与 resume 竞争、回填与运行并行）会产生重复 seq，时间线排序歧义。写入撞唯一冲突时重读 MAX 重试一次 |
| 3 | **`turn_index` 写入时记** | DB 里没有 `chat_round` 字段，查询时从 `dialogue_history` 派生脆弱且慢 |
| 4 | **token 优先走 `cost_tracker`** | jsonl 里 `response.usage` 实测只有 **15%** 覆盖率。回填时"有则取、没有留 0"，列表页 token 合计必须标注"仅基于部分调用"（`meta.has_usage`），不能把缺失当作 0 参与省钱类结论 |

---

## 5. 写入路径：三个统一埋点入口

运行时所有事件一律走 `trace_writer.py` 的三个入口，**不允许**各节点自行拼 exec_log + TraceWriter —— 分开写的结果就是「文件里有、库里没有」。

```python
# ① LLM 调用：文件明细（jsonl）+ 后台索引，一次调用双写
record_llm_turn(requirement_id, *, stage, iteration=None, model=None,
                system_prompt=None, prompt=None, messages=None, tools=None,
                response=None, thinking=None, latency_ms=None,
                turn_index=0, trace_id=None, label=None, status=None)

# ② 工具调用：同上
record_tool_call(requirement_id, *, name, arguments=None, result=None,
                 iteration=None, stage=None, turn_index=0, trace_id=None,
                 status=None)

# ③ 里程碑事件：意图/记忆/澄清/规划/确认/验收结论/修复/门禁/回滚/交付
record_event(requirement_id, kind, label, *, stage=None, status="ok",
             turn_index=0, trace_id=None, meta=None, duration_ms=None)
```

### 5.1 埋点纪律（全部有守卫测试盯）

| # | 规则 | 原因 |
|---|---|---|
| 1 | **`status` 自动推导** | `record_llm_turn` 在调用方未传 `status` 时从 `response.is_error` 推导。失败的调用记成 `ok` 会让列表页 `error_count` 失真，时间线上错误调用显示成功 |
| 2 | **`turn_index` / `trace_id` 全链路透传** | 一律从 `state["metadata"]` 取，与 `ToolCallLoop` 同一来源。规划/验收/修复的调用如果不传，多轮对话中这些事件会全部落到「初次生成」（turn 0）且 `trace_id` 为 NULL，轮次切换器数据错乱 |
| 3 | **`call_id` 经 contextvar 串联** | `call_id` 在 `llm/client.py` 请求发起时生成，通过 `log_context.bind_call_id()` 绑定到当前上下文；`record_llm_turn` 用 `current_call_id()` 取出落库。这是 `agent_events` ↔ `llm_traffic.log` 互查的唯一钥匙 |
| 4 | **埋点必须复用真实变量，禁止重拼字面量** | 记录用的 system prompt / 参数必须引用实际发送的那份。另写一份字面量，两处漂移后日志就开始说谎 |
| 5 | **写入失败只告警、不抛出，但计数** | 可观测性不得阻断主生成流程；但失败不能静默 —— 进程内计数器累计，经 `/api/admin/traces/stats` 透出，否则「后台没数据」和「没产生数据」无法区分 |
| 6 | **切面只认自己的节点** | `@_traced_node("X")` 挂错函数（HEAD 里 `defect_repair` 曾被挂在辅助函数 `_build_vision_images` 上）会造成双向事故：真节点没被包到（`repair` 事件永不出现），被误包的函数每次调用都在 `state.pop` 上抛 `AttributeError`（验收视觉模式直接打挂）。`_emit_node_milestone` 因此自证输入：`state` 必须是 dict、节点名必须在白名单内，否则静默返回 |
| 7 | **节点结论埋在包装层，不埋在各个 return 前** | `coder_node` 有四条返回路径（未注入 ToolCallLoop / 委派失败 / 抛异常 / 正常收尾），逐条埋必漏一条 —— 实测漏掉的正是「编码产出了几个文件」，13 类事件只缺 `coding`。包装层收口保证「节点执行一次 = 一条结论」 |

### 5.2 大体积工具参数：与 message 同一套 content 寻址

`write_file` 的 `arguments.content` 是完整产物文件，直接塞进 payload 会让每个写文件事件各存一份产物，3.2x 的压缩成果被吃掉。

规则（运行时与回填**同一套**，禁止一边截断一边全量）：

1. 工具参数中超过 `_MAX_ARG_LEN`（2000 字符）的字符串值，走 content 寻址落 blob
2. payload 里只留预览（前 2000 字符）+ `content_ref`（hash）
3. 详情接口按需经 blob 还原完整参数

---

## 6. 事件契约（阶段与类型的唯一定义处）

`harness/observability/event_contract.py` —— 新增阶段或事件类型**只改这一个文件**。

| 项 | 内容 |
|---|---|
| 阶段 | `planning` / `coding` / `verifying` / `repairing` / `delivering` |
| 下发方式 | `GET /api/admin/traces/contract`，前端据此渲染中文名、配色、筛选 chip |
| 兜底 | 未登记类型显示原名 + 中性灰，**不**编造中文名（否则分不清是新阶段还是脏数据） |

### 6.1 十三类事件：埋点位置与 meta 契约

契约里定义的每一类事件都必须有**运行时真实埋点**（不允许只存在于回填脚本），且 `meta` 必须能直接回答一个排查问题 —— 时间线不缺数据，缺的是语义。

| kind | 埋点位置 | meta 契约 | 回答的问题 |
|---|---|---|---|
| `intent` | 意图路由分类后 | `intent`, `confidence`, `skill_name` | 需求被路由到哪条路径（QUICK/SEARCH/TASK/SKILL/越界） |
| `memory` | `MemoryManager.inject_with_receipt` | `injected`（注入条数）, `hit_ids`（记账行主键） | 这次任务注入了哪些历史经验 |
| `clarify` | team_leader 返回 `needs_clarification` 处 | `reason`, `question_count` | 为什么被拦下澄清 |
| `plan` | team_leader 返回 `team_leader_done` 处 | `features`, `ac_count`, `complexity`, `dod_issues` | 规划产出了什么、是否带病放行 |
| `confirm` | `RequirementService.confirm_plan` | `has_feedback`, `feedback_len` | 用户何时确认了计划、是否附了修改意见 |
| `coding` | `coder_node` 包装层（覆盖委派 / 批量两条路径的全部 return） | `file_count`, `files` | 编码产出了多少文件 |
| `llm_turn` | 全部 LLM 调用（`record_llm_turn`） | `message_count`, `has_usage`, `thinking` | 这次调用发了什么、回了什么 |
| `tool_call` | ToolCallLoop 工具执行（`record_tool_call`） | `blocked`, `content_ref`, `arg_refs` | 工具做了什么、结果是什么、产物存在哪 |
| `verify` | verify 结论点（含快速通道与评估异常） | `verdict`, `score`, `findings`, `failed_ac_ids`, `critical_count`, `defect_count`, `ac_total`, `fast_pass` | **验收为什么过 / 为什么不过** |
| `repair` | defect_repair 每轮结束 | `round`, `target_defects`, `written_files` | 第几轮修了什么缺陷、改了哪些文件 |
| `quality_gate` | 交付门禁 + chat 轻量闸门 | `blocked`, `critical_count`, `unmet_acs`, `repair_rounds`（chat 侧另带 `defect_count`, `summary`） | 交付为什么被拦截 / 放行 |
| `rollback` | chat 闸门回滚点 | `restored_files`, `removed_files` | 回滚了什么 |
| `deliver` | 需求置为 `finished` 处 | `repair_rounds`, `gate`（strict/loose） | 何时交付、经过几轮修复 |

埋点位置原则：**节点级结论事件集中在 `_traced_node` 切面或节点返回点**，不撒胡椒面。新增事件类型时的检查顺序：契约登记 → 本表加一行 → 埋点 → 守卫测试。

两类事件在时间线上会**重复出现**，这是如实记录而非重复写：

- `memory`：澄清答完、计划确认后都会重新入队处理，每次处理重新注入一次记忆
  （`_build_injected_memory_block` 在生成阶段与定向补全阶段各调一次）。
  同一需求的多次 `memory` 事件若 `hit_ids` 相同，说明注入内容没变；
- `verify` / `repair`：验收未过会回到编码再验，天然多轮 —— 靠 `turn_index`
  与 `repair` 的 `round` 区分。卡片标签必须用**真实轮次**（`actual_round`），
  不能用封顶后的值，否则第 3、4 轮会挤在同一个标签上。

### 6.2 旧日志的阶段推断

`infer_stage_from_legacy(iteration)`：ToolCallLoop 埋点传 `iteration + 1`（从 1
起），verify / repair / AC 翻译走 `record_llm_turn` 固定传 0 —— 因此
`iteration == 0` 的旧记录必然属于验收链路。

这条判据一旦失效（ToolCallLoop 改成从 0 起），存量数据会集体标错阶段，
所以有守卫测试直接断言 `ToolCallLoop.run` 的源码里仍有 `iteration + 1`。

---

## 7. 写入时机（已落地的 P0）

**问题**：`end_trace()` 才落库 → 崩溃 / 中断 / 进行中全部丢失，覆盖率约 1/4。

**改动**：`start_trace()` 立刻 upsert 一条 `status=running` 的记录，`end_span()` 时更新既有行，`end_trace()` 写终态（`finished` / `error` / `interrupted`）。未正常关闭的 span 在 `end_trace` 时显式标记 `interrupted`，与正常完成区分。

`agent_traces` 保留 span 全量 JSON 的增量重写：span 量级（≤ ~60/trace）下重写成本可接受，且它是「中断任务的进度快照」的落点。`agent_events` 是查询面，`agent_traces` 是运行态摘要，两者职责不重叠。

**验收**：触发一次中途中断的任务，确认 `agent_traces` 中能查到该 trace 且 spans 不为空。

---

## 8. 性能设计

| # | 措施 | 收益 |
|---|---|---|
| 1 | **ToolCallLoop.run 内复用单一 writer**（自有 session，入口创建、finally 关闭） | 消除每事件一次 `MAX(seq)` 查询与 session 创建；seq 首查 DB 后内存自增 |
| 2 | **writer 内去重缓存**（已见 blob hash / tools_ref 内存集合） | 命中缓存跳过 blob 存在性查询与 tools_ref 查询，单事件 DB 往返从 ~5 次降到 2 次（payload + event） |
| 3 | **节点级低频事件保留短 session** | planning/verifying/repairing 每需求只发生几次，短 session 换取与主流程事务彻底解耦 |
| 4 | **seq 唯一冲突重试一次** | 并发写入兜底（见 §4.1-2） |
| 5 | **列表页只碰 L1** | 明细（response / 完整 messages）在点开某事件时才查 L2，列表页 GROUP BY 不扫大字段 |

去重缓存的正确性边界：只有**确认提交成功**的 hash 才登记为「已存在」，
本批新增的先进 pending，提交成功后才提升。反过来做（先记已存在再写）一旦
写入失败，正文此后永不补写，还原时表现为静默缺失 —— 而这正是本方案最早
踩过的坑（`missing=True` 的存在意义）。

---

## 9. 与其它日志通道的关系

| 通道 | 去向 | 关系 |
|---|---|---|
| `agent_events` 三表 | 后台「Agent 轨迹」唯一数据源 | 本方案 |
| `agent_exec/<req>.jsonl` | 开发排查明细（reasoning、原始 response） | 由 `record_llm_turn` 内部同步双写，调用方不感知 |
| `llm_traffic.log` | 传输层 request/response（截断 8000 字符） | **保留**：它是 KV-cache 命中率等离线指标（`metrics_report.py`）的数据源。经 `call_id`（contextvar）与 `agent_events` 互查。环境变量 `LLM_TRAFFIC_LOG=0` 可整体关闭 |
| `agent_traces` 表 | 运行态 span 摘要（中断可见进度） | 见 §7 |

---

## 10. API 设计

| 方法 | 路径 | 用途 | 主要字段 |
|---|---|---|---|
| GET | `/api/admin/traces` | 列表页 · 支持 `q`（关键词）/ `kind`（含某类事件）/ `status`（需求状态）筛选 | `requirement_id`, `title`, `status`, `turn_count`, `duration_ms`, `llm_calls`, `tool_calls`, `tokens`, `cost`, `last_event_at`, `error_count` |
| GET | `/api/admin/traces/<req_id>/events` | 时间线（阶段 → 迭代 内联分组，见 §10.2） | `seq`, `ts`, `kind`, `stage`, `label`, `status`, `model`, `duration_ms`, `tokens_in/out` |
| GET | `/api/admin/traces/<req_id>/turns` | 轮次切换器 + **需求级 `summary` 汇总**（见 §10.3） | `turn_index` / `started_at` / `ended_at` / `llm_calls` / `mode`；`summary` = `turns`、`events`、`llm_calls`、`tool_calls`、`tokens`、`cost`、`duration_ms`、`error_count` |
| GET | `/api/admin/traces/events/<event_id>` | 单事件详情 | 完整还原的 `request.messages`、`response`、`tool_calls`、大参数原文；外加 **`meta`（结论字段）**、**`trace_id` / `call_id`（追溯键）** |
| GET | `/api/admin/traces/contract` | 事件契约下发 | kinds / stages / filter_kinds / fallback |
| GET | `/api/admin/traces/stats` | 首页指标卡与筛选 chip 的唯一数据源（见 §10.3） | `by_kind`、**`by_status`（需求状态分布，与列表页同一分母）**、总量、`total_tokens` / `total_cost`、**`writer_failures`（埋点写入失败计数）** |
| GET | `/api/admin/traces/<req_id>/export` | 导出 Markdown | 单次 trace 的人读版本 |

全部接口 `@admin_required`（见 §1 可见性边界）。**列表页聚合**由 L1 直接 `GROUP BY requirement_id` 得出，不扫 L2。

### 10.1 结论字段必须一路走到界面

埋点写了、库里有了，不等于**排查时看得到**。这条链上有三处都能静默断掉，
每一处都是「后端做了、界面上没有」的半交付：

1. `meta` 没被详情接口返回 → 前端拿不到；
2. 详情接口返回了，前端没渲染 → 字段只躺在库里；
3. `call_id` / `trace_id` 没返回 → 界面无法从这条 LLM 事件跳到传输层原文，
   `llm_traffic.log` 的互查能力等于没有入口。

对应的落地形态：

- 详情接口显式返回 `meta` / `trace_id` / `call_id`（守卫：`tests/unit/test_admin_trace_detail.py`）；
- 详情面板概览条下方渲染 **「要点」** 区 —— 键名中文化（`failed_ac_ids` → 「未达成验收项」），
  数组截断显示、判定非 PASS / 被拦截 / 有 critical 的字段标红；
- 「要点」**只显示偏离默认值的字段**（`blocked=false`、`thinking=false`、`has_usage=true`
  以及内部标识 `content_ref` / `message_count` 不显示）。全渲染会让每个 tool_call
  都顶一行「已拦截 否」，结论区退化成噪声；
- 概览条上给出 `call_id` 前 8 位，点击复制完整值，用于 `llm_traffic.log` 定位。

### 10.2 界面形态：一栏时间线，不另设「执行树」

详情页曾是三栏：左「事件时间线」+ 中「执行树 · 按迭代分组」+ 右「事件详情」。
中栏已删除，理由是它**既重复、又名不副实**：

1. **重复**：中栏不请求任何额外接口，渲染的是左栏同一个 `filtered` 数组（同筛选
   chip、同点击回调）。左栏每条事件本来就带 `iter N`，中栏只是把同一字段抽出来
   当分组标题 —— 同一份数据的第二次渲染，却占了三分之一屏宽。
2. **名不副实**：`agent_events` **没有父子关系字段**，`/events` 也不返回层级，
   真正的调用树无从表示。「执行树」实际做的只是 `GROUP BY iteration`。
3. **分组轴用错维度**：`iteration` 对辅助链路是一个**哨兵值**（历史上传 `0`），
   不是迭代号。真实数据里「迭代 0」把 planning / verifying / repairing 三个阶段的
   11 条无关调用揉成一组，而同一批阶段的里程碑（verify / repair / quality_gate）
   `iteration` 为空、落在另一个叫「准备阶段」的组里 —— 同一次验收的两半被劈开。

**时间线（按时间排序）与分组（按归属分层）不是两种视图，而是同一条时间线的两个
属性**，所以层级内联在时间线里，取两级：

- **L1 阶段**（`stage`，契约里已定义 5 类）：按**连续段**切分。不做全局聚合 ——
  验收 → 修复 → 再验收 这种反复出现的阶段，聚合后会排成「所有验收在前、所有修复
  在后」，多轮修复的因果就读不出来。同一阶段被切成多段时，段头标注「第 N 次」。
- **L2 迭代**（`iteration`）：判据是 `iteration >= 1`，**不是**「阶段名是不是 coding」——
  让数据自己说话，将来别的阶段有了迭代号也自动生效。辅助链路不带迭代号，
  因此不会被塞进「第 0 轮」。不参与迭代的事件（如「编码收尾」里程碑）直接挂在阶段下。

折叠能力是合并后必须保住的（否则长链路比改版前更难读）：

- 折叠状态记「用户显式点过的组」+「未点过时的默认值」，**不预填 key** ——
  分组的 key 由构建器生成（`s:<stage>#<n>`），页面侧猜不出来；用数组下标预填会在
  换筛选后张冠李戴。
- 事件数 > `AUTO_COLLAPSE_OVER`（40）时默认折叠阶段，首屏先给一张「分了几段、
  每段几步」的目录；短链路默认全展开，改版不该把原本一眼可见的东西藏起来。
- key 含阶段名与「第几次」，保证换筛选后残留的折叠状态不会命中另一个组。

分组规则抽在 `frontend-vue/src/views/admin/timelineRows.ts`（纯函数、不依赖 Vue），
用真实事件断言：零丢失零重复、时间序单调、重复阶段标次、辅助调用不进迭代、
折叠剪枝、步数守恒。验证券 `tmp/verify-timeline-rows.mjs`
（**注意：`tmp/` 被 gitignore，该脚本不进版本库、CI 也不会跑**，属遗留欠账）。

### 10.3 界面上的指标与筛选

首页与详情页顶部各有一组指标，数据**全部来自接口**，不在前端另算 ——
同一个指标在两处各算一遍，早晚会算出两个数。

**首页（`AdminTracesView`）**

- **指标卡** 5 张，数据源 `/stats`：覆盖需求（分母 = 全部需求数）、事件总数（附
  LLM / 工具调用分解）、Token 总量、累计成本、**埋点写入失败**。最后一张是这套设施
  自身的健康度：写入失败只告警不抛出（不能阻断主生成流程），不看它就分不清
  「后台没数据」和「这次没产生数据」。
- **筛选**：关键词 + 状态 chip + 事件类型 chip。两种 chip 的取值都不是前端写死的白名单：
  - 状态 chip 的**计数**取自 `/stats` 的 `by_status`，与列表页同一分母；
  - 事件类型 chip 取自 `/contract` 的 `filter_kinds`，新增事件类型自动跟上。
- ⚠️ **`by_status` 必须用 outerjoin**：`agent_events` 里存在找不到 `Requirement` 行的
  孤儿事件（实测有 `requirement_id=0` 的 83 条）。列表页是 outerjoin、把它显示成「未知」；
  若 `/stats` 改用 inner join，各状态之和会比「覆盖需求」少，而「未知」会变成一个
  点进去永远为空的死 chip。`?status=unknown` 因此走 `Requirement.id IS NULL` 对齐这一口径。
  守卫：`tests/unit/test_admin_trace_list_stats.py`。

**详情页（`AdminTraceDetailView`）**

- 顶部「需求汇总」一行，数据源是 `/turns` 的 `summary`：总耗时、对话轮次、LLM 调用、
  工具调用、Token、成本、失败事件。
- 它与下方的**轮次切换器**刻意分开、并写明「不随轮次切换而变」：切换器管「看哪一轮」，
  汇总管「总共多少」。两者挨着又不加区分，会被读成「当前轮次的量」。
- 耗时口径 = `max(ts) - min(ts)` 的**墙钟跨度**，不是各事件 `duration_ms` 之和 ——
  后者会把并行 / 包含的耗时重复计入。守卫同上：造两个相隔 10s、各 `duration_ms=60s`
  的事件，断言结果为 10s 而非 120s。

---

## 11. 历史回填

`scripts/backfill_traces.py` 把存量 `agent_exec/*.jsonl` + `agent.log`（记忆注入 marker）回填进三张表：

- 幂等：同一需求重复执行先清空再写入，清空与写入同事务
- 阶段：新日志自带 `stage`；旧日志走 `infer_stage_from_legacy`（§6.2）
- token：有 usage 则取、没有留 0，`meta.has_usage` 如实标注（§4.1-4）
- 大参数：与运行时同一套 content 寻址规则（§5.2），不做有损截断

---

## 12. 分期计划

| 阶段 | 内容 | 状态 |
|---|---|---|
| **P0** | 写入时机改造（start 即落库 + 增量刷新 + 终态标记） | ✅ 已落地 |
| **P1** | 三张表 + `TraceWriter` + 五个 API + 前端页面 | ✅ 已落地 |
| **P2** | 三个统一埋点入口 + 13 类事件全部运行时埋点 + §5.1 埋点纪律 + §8 性能设计 | ✅ 已落地 |
| **P2.1** | 端到端验收：真跑需求补齐「单测测不到」的缺口（coding 缺失 / 装饰器挂错函数 / 结论字段到不了界面 / 文件数说谎） | ✅ 已落地 |
| **P2.2** | 界面形态收敛：删掉重复的「执行树」栏，时间线内联「阶段 → 迭代」两级分组；辅助链路停用 `iteration=0` 哨兵值（见 §10.2） | ✅ 已落地 |
| **P3** | L3 冷归档 + 保留策略 | 数据量涨上来后 |

P2.1 的由来：P2 全量单测 1069 条全绿，但真跑一条需求立刻暴露 4 个缺口 ——
**单测证明的是「埋点函数写对了」，证明不了「真实链路上每类事件都出现了」**。
逐条见 §5.1 第 6、7 条与 §10.1。这类缺口只能在真实执行里现形，
所以 e2e 验证券是交付物的一部分，不是可选项。

前端筛选 chip（`filter_kinds`）除 `llm_turn` / `tool_call` 外，还暴露
`plan` / `verify` / `repair` / `memory` 四类**结论性**事件 —— 排查时
「验收为什么没过」「第几轮修了什么」能一键筛出来，比在时间线里翻快得多。

---

## 13. 验收标准

- [x] 中断 / 进行中的需求能在列表中出现（P0 + 运行时实时写入）
- [x] 点开任意一次 LLM 调用，还原出的 `messages[]` 与原始 jsonl **逐字段一致**
      （含 `name` / `tool_calls` / `tool_call_id`，全量 34067 条实测丢失 0）
- [x] **多轮对话轮次归属正确**：第 N 轮对话的事件落在 turn N 而非 turn 0
      （单测锁定：里程碑事件的 turn_index / trace_id 必须从 `state.metadata` 透传）
- [x] **失败调用状态如实**：`response.is_error` 时事件 `status=error`，
      列表页 `error_count` 计入（单测：`test_status_derived_from_response_error`）
- [x] `call_id` 落库（经 `llm_traffic.log` 可互查同一次调用）
- [x] `writer_failures` 透出在 stats 接口
- [x] 大体积工具参数（`write_file` 产物）走 content 寻址，payload 只留预览
- [x] **结论字段走到界面**：详情接口返回 `meta` / `trace_id` / `call_id`，
      详情面板渲染「要点」区（守卫：`test_admin_trace_detail.py`）
- [x] **13 类事件在真实需求执行中成链**（12/13 已实测观察到）：
  - 全绿链（需求 213，32 事件）：intent → memory → clarify → plan → confirm →
    llm_turn/tool_call → verify → deliver
  - 失败链（需求 215，46 事件）：coding（收尾 max_iterations）→ verify 未过 →
    repair 失败 → verify 未过 → repair 失败 → verify 未过 → quality_gate（交付拦截）
  - **`rollback` 尚未在真实运行中观察到** —— 它只在 chat 编辑的轻量质量闸门回滚
    路径触发，需要真跑一次「对话改产物 + 产生确定性缺陷」才能看到；
    在此之前这条埋点只有源码级覆盖，属于待验项
- [x] **verify 事件可直接回答「为什么没过」**：界面上即显示 `failed_ac_ids` 与
      `critical_count`，无需逐条点开 LLM 调用（浏览器实测：未达成验收项 AC-4、
      严重问题 1、判定 NEEDS_WORK、得分 5.5）
- [x] 列表页加载不扫描 L2 明细：聚合 `FROM agent_events` 一张表，实测 7857 事件 /
      54 需求下 `Seq Scan + HashAggregate` **3.75ms**；1000 需求规模为线性外推
      （约 15 万事件、数十 ms 量级），**未实测**
- [x] **首页指标与筛选**：5 张指标卡全部取自 `/stats`；状态 chip 的计数与点进去的行数
      一致（实测 55 = 5+23+2+2+1+6+15+1，浏览器点「失败 5」恰好 5 行）；孤儿需求计入
      「未知」而非被丢掉（守卫：`test_admin_trace_list_stats.py`）
- [x] **详情页需求汇总**：`/turns` 的 `summary` 已在界面渲染（实测 #215：17m51s /
      1 轮 / 18 次 LLM / 14 次工具 / 67.3k token / ¥0.0827 / 4 个失败事件），
      耗时口径为墙钟跨度而非耗时求和
- [ ] **与设计稿逐项对齐**：首页指标卡与筛选条的**具体项**应由设计稿决定，但 ardot
      设计稿需 SSO 登录、本机打不开。当前项是按「现有数据算得出」挑选的，
      拿到设计稿截图后需再核一遍措辞与取舍

> 单测只覆盖埋点函数本身；「事件在真实链路上成链」必须真跑需求，
> 验证券：`backend/tmp/verify_observability_e2e.py`（`--rid N` 可附着已有需求）。

---

## 14. 未决 / 后续

- **`rollback` 待真实链路验证**：13 类事件里唯一还没在真实运行中观察到的。
  触发路径是 chat 编辑产物的轻量质量闸门回滚，验证方式：对一条已有需求发一条
  会改坏产物的对话，看闸门拦截后时间线上是否出现 `quality_gate(blocked)` +
  `rollback`。
- **保留策略**：默认全留（262 MB/千需求可接受），如需回收按时间淘汰 L2
- **`response` 未做 content 寻址**：当前占约 43%，如需进一步压缩可复用同一套 hash 机制
- **realtime 推送**：当前走的是 SSE 的实时事件通道，历史回溯走本方案的 API，两者暂不合并
- **列表页 1000 需求规模**：实测只有 54 需求 / 7857 事件（3.75ms），
  规模上限是线性外推，未实测
- **`filter_kinds` 未含 `quality_gate` / `rollback`**：失败类事件目前靠时间线里
  的红色标签识别，没有一键筛选入口。要加需先想清楚 chip 是否改多选
