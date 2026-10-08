# 评测集的过程与结果，放在哪里看

> 目标：评测跑完，能像运营后台看需求轨迹那样，在页面上看到**每一题的过程与结果**，
> 同时评测数据与运营库彻底分开。
>
> 存储：SQLite（一次运行一个库文件）。

---

## 一、结论

三句话：

1. **落点**：一次评测运行 = 一个自包含目录 `eval/runs/<run_id>/`，内含 `trace.db`（过程）与 `run.json`（结果摘要）。不改任何表结构。
2. **隔离**：把可观测性写入改道到独立库（评测默认指向该 SQLite）。运营库**在结构上不可能**被评测写入，而不是靠「记得加过滤条件」。
3. **查看**：后台加一个只读「评测」tab —— 运行列表 → 21 题 → 单题时间线。时间线渲染复用现有实现，不新写一套。

顺带解决 `run_id` 形同虚设（新增 `--run-id`，它现在真的是运行目录的名字）。
`IS_POSTGRES` 的判断方式**没有改** —— 那是 `config.py` 的全局行为，动它影响的是整个生产链路；
这里只在 `trace_db.py` 内部按 URL 前缀自己判方言，把 SQLite 该有的参数配齐，绕开即可（见 2.2）。


---

## 二、现状核实（结论都有代码出处）

### 2.1 隔离的地基是成立的

`config.py` 用 pydantic-settings 读 `.env`，其优先级是**环境变量 > .env 文件**。
已实测：`DATABASE_URL='sqlite:///...' python -c "from config import settings; print(settings.DATABASE_URI)"`
→ 输出 sqlite 串，且 `Base.metadata.create_all()` 在 SQLite 上建出全部 20 张表
（含 `agent_events` / `trace_message_blobs` / `agent_payloads`）。
`agent_events` 的 23 个列在 SQLite 下逐一落地，无类型降级问题。

也就是说：**在 `import models` 之前改环境变量，就能整体改道**。

### 2.2 但直接塞 SQLite 串会踩一个坑

```python
@property
def IS_POSTGRES(self) -> bool:
    return bool(self.DATABASE_URL)      # config.py
```

它判断的是「**有没有配** URL」，不是「URL 是不是 PG」。

后果：只要给 `DATABASE_URL` 塞一个 `sqlite://...`，`IS_POSTGRES` 就是 `True`，于是

- 不注册 `check_same_thread=False` → 子线程访问报 `SQLite objects created in a thread can only be used in that same thread`
- 不设 `journal_mode=WAL` / `busy_timeout` → 并发写直接 `database is locked`
- 反而把 `pool_size` / `max_overflow` / `pool_pre_ping` 这些 PG 参数传给 SQLite

所以「评测用 SQLite」这件事**不能靠改 `DATABASE_URL` 实现**。下面第四节给的做法绕开了它。

### 2.3 轨迹写入只有两个出口

| 位置 | 作用 |
|---|---|
| `harness/runtime.py` 的 `ToolCallLoop.run()` | 每个 run 建一个长 writer，`db = SessionLocal()` |
| `harness/observability/trace_writer.py::_open_writer()` | 降级路径：每事件一个短 session，同样 `SessionLocal()` |

两处都来自 `models.SessionLocal`。**把这两处换成同一个可覆写的取 session 函数，隔离就完成了** —— 不需要动 `TraceWriter` 内部（它只持有传进来的 `db_session`）。

其余用 `SessionLocal` 的地方（`state/memory.py`、`memory_retriever.py`、`tracer.py`、`checkpoint.py`）都是**业务数据**，本就该留在主库，不动。

### 2.4 另外两个小事实

- 路由注册方式是 `app.py` 里逐行 `import routes.xxx`（`@app.route` 直接挂 app，无 Blueprint）。
- `run_eval.py` 里 `run_id = getattr(args, "run_id", 时间戳)`，但**没有 `--run-id` 参数** → 它恒等于时间戳，且只用于临时工作区路径，没有传给任何落点。

---

## 三、落点：一次运行 = 一个自包含目录

```
eval/runs/<run_id>/           # run_id 形如 20261008_143036
├── trace.db                  # SQLite：agent_events / trace_message_blobs / agent_payloads
│                             #   表结构与线上完全一致，可直接套用现有查询与渲染
├── run.json                  # 运行元信息 + 逐题摘要（结果）
└── （不存 report.md，见下）
```

### `run.json` 结构（实际落盘）

```jsonc
{
  "run_id": "20261008_143036",
  "started_at": "2026-10-08T14:30:36", "finished_at": "2026-10-08T15:41:26",
  "duration_s": 4250.1,
  "model": "agnes-3.0-flash",
  "args": { "with_plan": true, "with_memory": false, "no_preview": false, "tasks": [] },
  "totals": { "total": 21, "passed": 21, "pass_rate": 100.0, "duration_s": 4250.1 },
  "full_set_size": 21,                       // 当时评测集的全量题数（读 tasks.yaml）
  "report_path": "eval/results/baseline_20261008_143036.json",
  "compare": {                               // 带 --compare 时才有
    "baseline_path": "eval/results/baseline_xxx.json",
    "baseline_run": "20261007_131532",
    "baseline_pass_rate": 90.5, "pass_rate": 100.0,
    "improved": 1, "regressed": 0,
    "improved_ids": ["t07"], "regressed_ids": [],
    "added_ids": [], "missing_ids": [],
    "changed": [ { "id": "t07", "name": "...", "before": false, "after": true, "error": "" } ]
  },
  "tasks": [
    { "id": "t01", "name": "个人名片页", "level": 1, "passed": true,
      "duration_s": 96.3, "rounds": 4, "plan_used": true, "plan_files": ["index.html"],
      "tool_sequence": ["read_file", "write_file+read_file"],
      "workspace": "", "error": "",
      "assertions": [ { "type": "preview_no_error", "passed": true, "detail": "" } ] }
  ]
}
```

`tasks[]` 就是现有 `TaskResult`（`run_eval.py`）的 `asdict()`，**已经有 plan_used / rounds / tool_sequence / assertions**，不需要新造字段。

**为什么 `run.json` 要单独存一份摘要**：运行列表页只读它（纯文件 JSON，零 SQLite 开销），
不必为了显示「21/21、耗时、成本」去开 21 个数据库文件。

**为什么报告仍留在 `eval/results/`**：那里有历史基线与 `latest.json` 软链，是既有习惯与
`--compare` / `--resume` 的输入。`run.json` 只记一个 `report_path` 指针，不搬家。

**两个字段是有具体教训的**（都不是「顺手加的」）：

- `full_set_size`：历史报告里混着大量调试期的单题/部分跑（`1/2`、`2/7`）。不标出全量题数，
  `2/7 = 28.6%` 和 `21/21 = 100%` 会在列表同一列并排，被直接读成「成绩掉了一大截」。
  前端据此打「部分题」标记（列表 chip + 详情页头 chip + 题量卡副标题）。
- `compare.regressed_ids`：对比里唯一需要立刻看见的信号是「从过变挂」，其余降级成小 chip。

**时间口径**（踩过）：`agent_events.ts` 存 UTC naive，接口经 `_iso_utc` 补 `+00:00` 下发，
浏览器按本地时区渲染；`run.json` 的 `started_at` 由 `run_eval` 用 `localtime` 写，本身就是本地时间。
两者最终都渲染成本地时间 —— 前提是**两边写的是同一个时刻**。这个约定必须写在 `eval/runs/` 的
造数脚本里，否则页面上同一时刻会出现两个钟点（实测汇总卡 12:00、时间线 20:00）。


---

## 四、隔离：可观测性写入改道独立库

### 新增 `backend/harness/observability/trace_db.py`

一个薄封装，暴露 `trace_engine()` / `trace_session()`：

- 读环境变量 `TALK2CODE_TRACE_DB`
- **为空** → 直接返回 `models.SessionLocal`（**生产行为一字不变**）
- **非空** → 自建 engine 指向该 URL；方言是 sqlite 时显式传
  `connect_args={"check_same_thread": False}` 并注册 `journal_mode=WAL` + `busy_timeout`
  （即 **绕开 2.2 的 `IS_POSTGRES` 误判**，自己把 SQLite 该有的参数配齐）
- 首次调用时对目标库 `create_all(checkfirst=True)`，评测进程不需要先跑 `init_db()`

改两处调用点即可（第二节 2.3 的两个出口）。

### 评测进程怎么用

`run_eval.py` 在 `import harness` **之前**（文件顶部 `sys.path.insert(backend)` 之后）：

```
os.environ["TALK2CODE_TRACE_DB"] = f"sqlite:///{run_dir}/trace.db"
```

**只设这一个变量就够** —— 主库（`DATABASE_URL`）保持 `.env` 里的 PG 不动，于是：

| 数据 | 去向 |
|---|---|
| `agent_events` / blobs / payloads（过程） | 本 run 的 `trace.db` |
| `agent_memories_v2` / `memory_hits`（记忆） | 主库 PG（`--with-memory` 时照常工作） |

这样 `--with-memory` 的记忆 A/B 不受影响 —— 读记忆、写归因都还在 PG，
只有轨迹被隔离开。默认（不带 `--with-memory`）则完全不碰 PG。

### 题号仍用 1..21

`requirement_id` 继续取题号，因为**一个 run 库内需要靠它分组 21 题**；
库已隔离，不存在「借号污染」。`trace_id` 填 `run_id`，便于跨表追溯。

---

## 五、查看：后台只读「评测」页

### 接口（新增 `backend/routes/admin_evals.py`，全部 `@admin_required`）

| 方法 | 路径 | 返回 |
|---|---|---|
| GET | `/api/admin/evals` | 运行列表（扫 `eval/runs/*/run.json`，按时间倒序） |
| GET | `/api/admin/evals/<run_id>` | 该次运行的逐题摘要 + 每题事件数 |
| GET | `/api/admin/evals/<run_id>/tasks/<task_id>/events` | 单题时间线 |
| GET | `/api/admin/evals/<run_id>/tasks/<task_id>/turns` | 单题 LLM 调用明细 |
| GET | `/api/admin/evals/<run_id>/events/<int:event_id>` | 单事件详情（prompt 展开） |

**后三个的响应结构与 `/api/admin/traces/<rid>/*` 逐字段一致** —— 这是前端能复用渲染的前提，
也是要写进测试的契约（防两套接口各自演化）。

安全：`run_id` 必须过白名单校验（仅 `[A-Za-z0-9_-]`），`task_id` 仅 `t\d+`，
否则 `<run_id>` 传 `../..` 就能读任意 SQLite 文件。这是必须有的守卫。

### 页面

| 路由 | 页面 | 内容 |
|---|---|---|
| `/admin/evals` | 运行列表 | 运行标识 / 时间、通过率（数字 + 横条 + 百分比）、总耗时、模型、配置 chips（真实规划 / 记忆注入 / 部分题 / 回归 / 改善）、操作 |
| `/admin/evals/:runId` | 单次运行 | 左：题列表（通过/未过着色、耗时、轮数、事件数、未达成的断言；可只看未过）；右：选中题的**两栏**（时间线 + 事件详情），与需求轨迹页共用组件 |

三个刻意的读法约定：

1. **通过率分三档**：100% 绿、≥80% 橙、<80% 红。两档（全过/有挂）会让 `19/21` 与 `2/7` 同色，
   而这两件事的处理方式完全不同（前者看回归，后者看环境）。
2. **「部分题」必须显眼**：`--tasks t01 t02` 或历史子集跑都打标。理由见上节 `full_set_size`。
3. **「无过程数据」不给可点入口**：只有 `run.json` 的历史运行，操作列显示灰字而不是链接。

时间线复用 `views/admin/timelineRows.ts`（纯函数，零改动）+ 从 `AdminTraceDetailView.vue`
抽出的渲染组件（`TraceTimeline.vue` / `EventDetailPanel.vue`）。效果是：**和「需求轨迹」详情页
是同一套时间线**，只是数据源换了。

### 进行中（跑的时候也能看）

这是后补的一条通路，起因是个真实的使用问题：**结果要跑完才有，过程却一直在写**。

- `run.json` 是收尾时写的（逐题攒完后 `write_run_manifest` 一次性落盘）；
- `trace.db` 从**第一次埋点**起就在实时写。

只认 `run.json` 的话，一次全量评测（本次实测约 60 分钟）在整个过程中于页面上
**完全不存在** —— 列表里没有这条运行，也就无从「跑的时候看着」。

所以 `routes/admin_evals.py` 加了一个反推函数 `_derive_running(run_dir)`：
`run.json` 缺失但 `trace.db` 有事件时，从过程库还原出这次运行。

反推的把握来自一个既有约定：**评测写入的 `requirement_id` 就是题号**
（`t01` → `1`），所以 `group by requirement_id` 就能还原出跑到第几题、每题多少事件。

反推出的字段分三类，处理方式刻意不同：

| 能推的 | 怎么推 |
|---|---|
| 题目列表 | `distinct requirement_id` → 题号，题名查 `eval/tasks/tasks.yaml` |
| 开始时间 | `min(ts)`（UTC naive，出参加过 `_iso_utc`） |
| 每题事件数 | `count(*) group by requirement_id` |
| 模型名 | 任取一条 `model` 非空的事件（有快照时以快照为准） |
| `with_plan` | 有没有 `kind='plan'` 的事件（`--no-plan` 时不会有） |
| 每题**结论** | 读进度快照 `progress.json`（见下）|

| **不能推的，一律留空** | 为什么 |
|---|---|
| `duration_s` / `pass_rate` | `None`。宁可为空也不编一个出来 |
| 没有快照时的每题 `passed` | 置 `None`（不是 `False`）。「还没评」和「评了没评过」是两件事 —— 落成 `False` 会让中途的页面显示一个假的失败数 |

#### 进度快照：跑完一题就落一次（`progress.json`）

只靠反推还有一个说不清的地方：**已开始的题全被标成「进行中」**，看不出评测是
一题跑完才起下一题的。根因是结论只在收尾的 `run.json` 里，过程中无处可查。

所以 `run_eval.py` 每跑完一题就写一份 `progress.json`（含已完成题的完整
`TaskResult`），原子替换（先写 `.tmp` 再 `os.replace`）以免后台读到半截 JSON。
`run.json` 落盘时由 `write_run_manifest` 顺手删掉它 —— 「progress → 结果」
这个交接只有一处实现，不留两个结果源。

于是「跑完没跑完」有了两个各司其职的判据：

| 状态 | 判据 | 页面 |
|---|---|---|
| 有结论 | 出现在 `progress.json` 里 | 通过 / 未过 + 耗时 + 轮次 |
| 跑完了但结论还没落盘 | 没有快照（老运行）时按时间序推：**后面已有别的题在写事件** → 这题必然跑完了 | 「已完成」（中性色） |
| 正在跑 | 事件最新的那一题 | 「进行中」（活动色） |

`totals.finished`（已跑完题数）与 `totals.total`（已开始题数）刻意分开：
列表页显示「N 题已跑完」，用「已开始」会虚高一题（把正跑着的那题算成已完成）。

前端配套三条：

1. **进行中不显示通过率**。`0/3 = 0%` 不是成绩，是「还没跑到」；同理题列表的
   徽标分四种（通过 / 未过 / 已完成 / 进行中），`passed === null` 走独立分支，
   不能 `!t.passed`。
2. **自动刷新**。有进行中的运行时每 15 秒静默刷新；跑完自动停，不留空转定时器。
   刷新走的是 `refreshLive()` 而不是 `loadRun()` —— 后者会「默认选第一道未通过的题」，
   每 15 秒把用户正在看的题重置掉，等于边看边被抢走视线。
3. **「已跑完但结论未知」不给任何暗示**。中性灰，不是绿也不是红 —— 它只是还没汇总。

一个踩过的坑：反推时 `args` 是空的，前端 `a.with_plan ? '真实规划' : '跳过规划'`
把 `undefined` 当成了 `false`，于是一次**走真实规划**的运行被显示成「跳过规划」——
比留空糟糕得多。现在前端只在 `typeof a.with_plan === 'boolean'` 时才写这一项，
后端则从 `plan` 事件把 `with_plan` 推出来（有快照时以快照为准）。

### 评测只覆盖「规划 + 编码」两个阶段（为什么没有验收）

评测页的时间线上只会出现**规划**与**编码**两个阶段。这不是丢数据，是评测的口径：

- `run_eval.py::run_one_task` 只驱动两个节点：`plan_for_task()`（真实 TeamLeader 规划）
  与 `coder_node()`（真实编码 + 完成度契约 + Phase 2 补全）；
- 平台线上的**验收（`verify`）/ 修复（`repair`）/ 质量门禁 / 回滚 / 交付**在评测里不跑。
  评测的「过没过」由 `AssertionChecker` **离线**判定 —— 对产物做确定性断言
  （`file_exists` / `content_contains` / `html_has_element` / 起一次浏览器看有没有报错），
  而不是走那条「评估器 LLM + AC 脚本」的链路。

换来的是**可比性**：断言是确定性的，同一份产物每次都判同样的结果，21 题的成绩
才能跨版本、跨模型横向比。

代价也要说清楚：**评测成绩不覆盖平台自己的验收/修复闭环**。所以「评测 21/21」
只说明「生成阶段能产出满足断言的产物」，不等于「在平台上端到端一定通过」。
若把验收阶段接进评测，分数会立刻依赖评估器与 AC 脚本的随机性，
历史基线（那一串 `2/7` → `19/21` → `21/21`）就不再可比 —— 那是另一条口径，
要单独记一列，不能混进现有这一列。


### 降级（必须做）

- `eval/runs/` 不存在（生产常态）→ 列表页显示「暂无评测运行」并说明数据从哪来，不是 500。
- 某次运行只有 `run.json` 没有 `trace.db`（历史数据补齐的情况）→ 结果照常看，
  详情页顶部一条提示 + 过程区显示「本次运行没有过程数据」。
- `progress.json` 停在半截（进程被杀）→ 读方按「还没有快照」处理，页面退回「已完成/进行中」，
  不会报错；但那条运行会一直显示「进行中」直到下一次真跑覆盖它 —— 判据是「有没有 `run.json`」，
  没有心跳检测。
- **`--run-id` 是「目录名」而不是「清空并新建」**：复用同一个 id 复跑会把两次运行混进同一个
  目录（`trace.db` 追加、`progress.json` 留存）。默认时间戳命名不会撞上。

### 离线兜底（不起服务也能看）

```
--list-runs              列出已有运行（含被补齐的历史，标 [仅结果]）
--print-run <run_id>     纯文本打印该次运行的逐题结果与配置
--backfill-runs          把历史 eval/results/baseline_*.json 补成 eval/runs/<ts>/run.json
```

`--print-run` 的表格按**显示宽度**补齐（中日韩字符占 2 列）：用 `f"{s:24s}"` 按字符数补，
中文任务名会把整列错开，终端输出只有「对齐」这一个可读性手段。


---

## 六、改动清单（实际落地）

### 后端

| 文件 | 改动 |
|---|---|
| `harness/observability/trace_db.py` | **新增**。`trace_engine()` / `trace_session()` / `is_redirected()` / `reset_for_tests()` |
| `harness/runtime.py` | 取 session 改走 `trace_session()`（1 处） |
| `harness/observability/trace_writer.py` | `_open_writer()` 同样改走（1 处） |
| `routes/admin_traces.py` | 抽出 `build_events_payload` / `build_turns_payload` / `build_event_detail` 与 `_use_db`，三个 route 变薄包装（行为不变） |
| `routes/admin_evals.py` | **新增**。5 个只读接口，后三个复用上面的 `build_*`；`_derive_running()` 从 `trace.db` 反推进行中的运行，并读 `progress.json` 取已完成题的真实结论（含 `finished` 三态） |
| `app.py` | 加一行 `import routes.admin_evals` |
| `eval/run_eval.py` | 延迟导入管线；`--run-id`；`redirect_trace_db()` / `write_run_manifest()` / `prune_runs()` / `compare_diff()`；离线子命令 `--print-run` / `--list-runs` / `--backfill-runs`；`write_progress()` 每题落一次进度快照（`run.json` 落盘时删掉） |
| `.gitignore` | 加 `eval/runs/` |

### 前端

| 文件 | 改动 |
|---|---|
| `types/trace.ts` | **新增**。两页共用的响应类型（一份定义，避免字段漂移） |
| `utils/format.ts` | **新增**。耗时/时间/计数格式化的唯一实现 |
| `components/admin/TraceTimeline.vue` | **新增**。抽出的时间线渲染（含筛选 chip 与折叠） |
| `components/admin/EventDetailPanel.vue` | **新增**。抽出的事件详情（meta 中文化、工具入参结构化、分页签） |
| `views/admin/AdminTraceDetailView.vue` | 1396 行 → 薄壳：只留数据加载 / 轮次切换 / 拖拽 |
| `views/admin/AdminEvalsView.vue` | **新增**。运行列表（含「进行中」行与 10 秒自动刷新；进度按「已跑完」而非「已开始」计） |
| `views/admin/AdminEvalDetailView.vue` | **新增**。题列表 + 两栏过程（含「进行中」进度态与 15 秒静默刷新；题徽标四态：通过/未过/已完成/进行中） |
| `router/index.ts` | 两条路由 |
| `components/admin/AdminShell.vue` | 新分组「研发」+「评测集」入口 |
| `scripts/eval-page-check.mjs` | **新增**守卫（39 项） |
| `package.json` | `check:eval-page`，挂进 `check` |


---

## 七、守卫与测试

| 测试 | 条数 | 钉住什么 |
|---|---|---|
| `tests/unit/test_eval_isolation.py` | 8 | 未设变量时 `trace_session` 就是 `models.SessionLocal`；设了 SQLite 后 engine 与主库分离、目标库自动建表、事件**不落到主库**、子线程可写；两处调用点不得回退成 `SessionLocal()` |
| `tests/unit/test_admin_evals.py` | 32 | 路径遍历（`../../`）返回 404；目录缺失/无 `trace.db` 时返回空态而非 500；单题 events / turns / 单事件详情的**字段集与 traces 接口逐字段一致**；`admin_evals` 必须复用 `build_*`（不得各写一份组装）；**进行中的运行**：只有 `trace.db` 也能列出来、无快照时 `passed` 必须是 `None` 而不是 `False`、耗时与通过率不得编造、空库不算一次运行、`with_plan` 从 `plan` 事件反推；**进度快照**：有快照时用真实结论、串行下已跑完的题 `finished=True`、单题运行仍算进行中、半截 JSON 静默降级 |
| `tests/unit/test_eval_pipeline_and_budget.py` | 18 | 评测默认走真实规划 + `coder_node`；`write_progress` 原子落盘（不留 `.tmp`）、字段含 `tasks_total`、`run.json` 落盘后快照被清掉；AST 数主循环里 `snap()` 的次数 = 追加 `results` 的分支数（漏一处就是整轮里有一类题永远不出现，且不报错） |
| `scripts/eval-page-check.mjs` | 40 | 两个详情页都只**引用**共享组件，谁把 `buildTimelineRows` 或 `evt-dot`/`grp-head` 搬回自己文件里就判失败；评测页不得打 `/api/admin/traces/*`（串库）；路由与导航入口存在；部分题标记两页同源；通过率三档不许被压成两档；**进行中**两页都有独立渲染分支（不得显示通过率）、题状态四态（不得把 `null` 当 `false`）、参数未落盘时不得渲染「跳过规划」、**跑完的题不得也标成「进行中」**、进度按「已跑完」统计、不得再声称「结果要等跑完才落盘」、必须说明「验收/修复」筛选为空是口径而非丢数据 |

`test_admin_evals.py` 里那条「必须复用」的判据不能用 `'items': items` 这类泛化片段 ——
逐题摘要里也是合法用法，会误判。判据要用时间线独有字段（`has_payload` / `message_count` /
`by_model` / `cache_hit_rate` / `first_llm_ms` 均不得出现在 `admin_evals.py`）。

---

## 八、落地状态

| 批次 | 内容 | 状态 |
|---|---|---|
| **P0 隔离** | `trace_db.py` + 两处调用点 + eval 侧环境变量 + `--run-id` + `run.json` + `.gitignore` | 完成。已实测：改道后主库同 `trace_id` 为 0 条，事件全在 run 目录 |
| **P1 可见** | `admin_evals.py` + 两个前端页 + `TraceTimeline` / `EventDetailPanel` 抽取 + 守卫 | 完成。真开浏览器逐页验证过（含需求轨迹页回归） |
| **P2 对比与补齐** | `--compare` 结构化落盘 + 回归横幅；`--backfill-runs` 补历史；`--print-run` / `--list-runs` | 完成。补齐只取最近 KEEP_RUNS 次，避免写完再删 |

保留策略：`eval/runs/` 只留最近 **5** 次（`KEEP_RUNS`，`--backfill-runs` 与每次跑完都会 prune）。
目录名是 `%Y%m%d_%H%M%S`，字典序即时间序，不需要读文件内容。
注意 **正在跑的那次也占一个名额**（它的目录已经建好了），所以「5 次」在运行期间
读起来是「4 次历史 + 1 次进行中」。要留更长的演进史就调大 `KEEP_RUNS`（一处常量）。

`eval/runs/` 是生成态（已在 `.gitignore` 里）。本机验证期曾造过一份合成运行用于
验收页面通路，**已随本次收尾删除** —— `eval/runs/` 里现在只剩真实跑出来的运行
（4 次历史补齐的只有结果摘要，1 次是真跑、含完整过程库）。


---

## 九、三个待拍板点的结论

1. **时间线组件怎么抽** → 取**甲**：抽 `TraceTimeline.vue`，需求轨迹页与评测页共用一套渲染。
   代价是回归一遍 `AdminTraceDetailView`（1396 行 → 薄壳）——已回归并通过
   （真开浏览器复核：汇总卡、阶段时间线、分组头、右侧详情、重复迭代号的 `· 第 N 次` 去重）。
2. **要不要补历史** → 补。`--backfill-runs` 只补最近 5 次（历史共 33 份，多数是调试期单题跑）。
   补齐的目录只有 `run.json`，页面会显示「无过程数据」，不给可点入口。
3. **保留策略** → 最近 **5** 次（`KEEP_RUNS`）。

