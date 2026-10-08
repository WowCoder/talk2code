#!/usr/bin/env node
/**
 * 评测集页面的结构性守卫。
 *
 * 回归对象（都是「不报错、只是看着怪 / 静默走偏」的那类）：
 *  1. **时间线被复制成第二份**。需求轨迹页与评测页必须渲染同一条时间线 ——
 *     分组规则一改只有一边跟上，两边看到的东西就不再可比，而「可比性」正是
 *     评测存在的理由。判据：两个详情页都只**引用**共享组件，谁把
 *     `buildTimelineRows` 或时间线内部的类名（`evt-dot` / `grp-head`）搬回自己
 *     文件里，谁就判失败。
 *  2. **接口串库**。评测页必须打 `/api/admin/evals/*`，打到 `/api/admin/traces/*`
 *     就会去运营库里找评测题号 —— 线上能跑通、本地全空，最难查的一类。
 *  3. **入口丢失**。路由/导航少一处，页面就只剩「知道 URL 的人能进」。
 *
 * 用法：npm run check:eval-page   （在 frontend-vue 目录下）
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const SRC = join(here, '..', 'src')

const read = (rel) => readFileSync(join(SRC, rel), 'utf8')
const traceDetail = read('views/admin/AdminTraceDetailView.vue')
const evalList = read('views/admin/AdminEvalsView.vue')
const evalDetail = read('views/admin/AdminEvalDetailView.vue')
const timeline = read('components/admin/TraceTimeline.vue')
const detailPanel = read('components/admin/EventDetailPanel.vue')
const router = read('router/index.ts')
const shell = read('components/admin/AdminShell.vue')

let checks = 0
const ok = (cond, msg) => {
  checks++
  assert.ok(cond, `✗ ${msg}`)
  console.log(`  ✓ ${msg}`)
}

// ---- 1. 共享渲染：两个详情页都必须引用同一套组件 ----
for (const [name, src] of [['需求轨迹详情页', traceDetail], ['评测详情页', evalDetail]]) {
  ok(src.includes("from '@/components/admin/TraceTimeline.vue'"),
     `${name}引用了共享时间线组件`)
  ok(src.includes("from '@/components/admin/EventDetailPanel.vue'"),
     `${name}引用了共享事件详情组件`)
}

// 反回退：谁把时间线的分组逻辑或内部渲染搬回自己文件，就抓出来
const TIMELINE_MARKERS = ['buildTimelineRows', 'evt-dot', 'grp-head', 'evt-body']
for (const [name, src] of [['需求轨迹详情页', traceDetail], ['评测详情页', evalDetail]]) {
  const hits = TIMELINE_MARKERS.filter((m) => src.includes(m))
  ok(hits.length === 0, `${name}没有内联一份时间线实现${hits.length ? `（发现 ${hits.join(', ')}）` : ''}`)
}

// 分组规则唯一实现处：共享组件引用它，页面不得直接引用
ok(timeline.includes("from '@/views/admin/timelineRows'"),
   '共享时间线组件引用唯一的分组规则（timelineRows）')
ok(!evalList.includes('timelineRows'), '评测列表页不掺和时间线分组')
ok(detailPanel.includes('toolArgs') || detailPanel.includes('ARG_LABEL'),
   '事件详情组件自己负责工具入参的结构化')

// ---- 2. 接口前缀：评测页不得打运营库 ----
ok(evalList.includes('`/api/admin/evals`') || evalList.includes("'/api/admin/evals'"),
   '评测列表页读 /api/admin/evals')
ok(evalDetail.includes('/api/admin/evals/${'), '评测详情页全程走 /api/admin/evals/*')

// 评测页里出现 traces 前缀（契约接口除外）就是串库信号
const strayTraces = evalDetail
  .split('\n')
  .filter((l) => /\/api\/admin\/traces\/(?!contract)/.test(l))
ok(strayTraces.length === 0,
   `评测详情页没有误打运营库接口${strayTraces.length ? `：${strayTraces[0].trim()}` : ''}`)

// ---- 3. 路由 + 导航入口 ----
ok(/path:\s*'\/admin\/evals'/.test(router), '路由注册 /admin/evals')
ok(/path:\s*'\/admin\/evals\/:runId'/.test(router), '路由注册 /admin/evals/:runId')
ok(shell.includes('to="/admin/evals"'), '侧边栏有「评测集」入口')

// ---- 4. 页面关键抓手：失败可筛、回归可见、部分题不被误读 ----
ok(/未过/.test(evalDetail) && /taskFilter/.test(evalDetail),
   '评测详情页能只看未通过的题')
ok(/regressed_ids/.test(evalDetail), '评测详情页透出「从过变挂」的题号')
ok(/regress-id|regress-chip/.test(evalDetail), '回归题号可点击跳转')
ok(/has_trace/.test(evalDetail) && /has_trace/.test(evalList),
   '无过程库的运行在列表与详情都标出来（不给点了没反应的入口）')

// 部分题跑：不标出来时 `2/7 = 28.6%` 会和 `21/21 = 100%` 在同一列并排，
// 被读成「成绩掉了一大截」。判据必须在两页各有一份（各自渲染各自的表格/卡片）。
ok(/full_set_size/.test(evalList) && /full_set_size/.test(evalDetail),
   '列表与详情都读 full_set_size')
ok(/partial\(/.test(evalList), '列表页判定「部分题」')
ok(/isPartial/.test(evalDetail), '详情页判定「部分题」')
ok(/部分题/.test(evalList) && /部分题/.test(evalDetail),
   '部分题在列表与详情都有可见文案')
// 两处判定必须同源：任一处漏掉 args.tasks 就会与另一处结论不一致
ok(/args\.tasks\?\.length/.test(evalList) && /args\.tasks\?\.length/.test(evalDetail),
   '两页的部分题判据都认「显式指定子集」这一路')

// 通过率三档：19/21(90.5%) 与 2/7(28.6%) 同色就分不出「差一点」和「没跑起来」
ok(/rate-fill\.warn/.test(evalList) && /'warn'/.test(evalList),
   '通过率分三档（100% / ≥80% / <80%），不是「有挂就红」')

// ---- 5. 空态要交代数据从哪来 ----
ok(evalList.includes('eval/runs'), '评测列表空态说明数据来自 eval/runs')

// ---- 6. 进行中的运行：看得到过程，同时不给出假结论 ----
// 一次全量评测一个多小时。只认 run.json（跑完才写）的话，整个过程里这次运行
// 在页面上完全不存在 —— 「跑的时候看着」就无从谈起。这条路必须一直在。
ok(/\.running\b/.test(evalList) && /\.running\b/.test(evalDetail),
   '两页都识别「进行中」状态')
ok(/进行中/.test(evalList) && /进行中/.test(evalDetail),
   '进行中在两页都有可见文案')
// 进行中最容易出的错：显示 `0/3 = 0%` —— 那不是成绩，是「还没跑到」
ok(/v-if="r\.running"/.test(evalList),
   '列表页对进行中的行单独渲染（不显示通过率）')
ok(/v-if="run\.running"/.test(evalDetail),
   '详情页对进行中的运行单独渲染')
// 三态：null（还没结论）不能被当成 false（未过），否则中途会显示假的失败数
ok(/if \(t\.passed !== null\) return t\.passed \? '通过' : '未过'/.test(evalDetail),
   '题状态区分「未过」与「还没结论」')
ok(!/filter\(t => !t\.passed\)/.test(evalDetail),
   '「未过」筛选不把「还没结论」算进去')
// 参数未落盘时曾经把 undefined 当 false，渲染出与事实相反的「跳过规划」
ok(/typeof a\.with_plan === 'boolean'/.test(evalDetail),
   '参数未落盘时不显示「跳过规划」（不把 undefined 当 false）')
ok(/syncLivePolling|refreshLive/.test(evalDetail),
   '进行中时页面自动刷新进度')

// ---- 7. 串行推进：跑完的题不能也标成「进行中」 ----
// 评测是一题跑完才起下一题。把「已开始」当成「都在跑」，一屏看过去像 8 道题
// 同时在跑，完全看不出它是逐题推进的 —— 而后端每跑完一题就写一份 progress.json，
// 结论本来就是现成的。
ok(/finished/.test(evalDetail), '详情页读每题的 finished 状态')
ok(/t\.finished \? '已完成' : '进行中'/.test(evalDetail),
   '「跑完了但结论还没落盘」与「正在跑」分开渲染')
ok(/已完成 \{\{ finishedCount \}\} 题/.test(evalDetail),
   '详情页进度按「已跑完」统计（已开始含正跑着的那题，会虚高一题）')
ok(/r\.totals\.finished/.test(evalList),
   '列表页进度同样按「已跑完」统计')
// 旧文案「逐题结果会在运行结束后一次性落盘」已经不成立（现在逐题出结论）
ok(!/一次性落盘/.test(evalDetail), '不再声称「结果要等整个运行跑完才落盘」')
// 评测只覆盖生成阶段：不说明的话「验收 / 修复」筛选永远为空，会被当成埋点丢了数据
ok(/scope-note/.test(evalDetail) && /不经过平台那条「验收 \/ 修复」链路/.test(evalDetail),
   '详情页说明评测只跑「规划 + 编码」，验收/修复筛选为空不是丢数据')

console.log(`\n评测集页面守卫通过：${checks} 项`)
