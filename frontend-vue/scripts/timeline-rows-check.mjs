#!/usr/bin/env node
/**
 * 用**真实事件**验证「阶段 → 迭代」两级分组构建器。
 *
 * 为什么要有这个脚本：分组规则写错了，界面上只表现为「看着有点怪」，
 * 肉眼盯截图基本抓不住（少一条、串一组、顺序被聚合打乱）。更隐蔽的一类是
 * **key 重复** —— 它不报错、界面只是「点一下展开一堆、展开内容还跑到别的
 * 迭代下面」，没有断言盯着就一定会漏到线上（需求 220 实测 5 个 key 重复）。
 *
 * 真实数据来自 DB（backend 导出，只保留 8 个结构化字段，无正文 / 无密钥）。
 * 断言覆盖：零丢失零重复、时间序单调、重复阶段标次、辅助调用不进迭代、
 * **行key 全局唯一**、**折叠互不串扰**、折叠剪枝。
 *
 * 为什么数据要带上 220：213/215 的编码阶段只跑了一轮ToolCallLoop，
 * iteration 单调递增，「迭代号回退 → 同key 重复」这条路径在它们身上
 * 永远不会触发。守卫数据集不覆盖的场景，守卫就是装饰。
 *
 * 用法：npm run check:timeline   （在 frontend-vue 目录下）
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadTsModule, projectRoot } from './_tsm.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const { mod, cleanup } = await loadTsModule('src/views/admin/timelineRows.ts')
const { buildTimelineRows } = mod

const data = JSON.parse(
  readFileSync(join(here, 'timeline-rows-cases.json'), 'utf8'),
)

let checks = 0
const ok = (cond, msg) => {
  checks++
  assert.ok(cond, `✗ ${msg}`)
  console.log(`  ✓ ${msg}`)
}

const ctx = {
  isOpen: () => true,
  label: (s) => ({ planning: '规划', coding: '编码', verifying: '验收', repairing: '修复', delivering: '交付' }[s] ?? (s || '')),
  color: () => '#888',
}

// key 形如 s:<stage>#<n>（阶段）或 s:<stage>#<n>@<iter>（迭代）
const stageOf = (key) => key.slice(2).split('#')[0]
// 迭代组的 key 去掉 @ 段，得到它所属的阶段 key（`s:coding#1@3~2` → `s:coding#1`）
const stageKeyOf = (key) => key.slice(0, key.indexOf('@'))
// 从key 解析组号：`s:coding#1@3` → 3；`s:coding#1@3~2` → 3（第 2 次出现）
const iterNumOf = (key) => Number(key.slice(key.indexOf('@') + 1).split('~')[0])
const hasIter = (e) => e.iteration !== null && e.iteration !== undefined && e.iteration >= 1

for (const [rid, events] of Object.entries(data)) {
  console.log(`\n=== 需求 ${rid}（${events.length} 条事件）===`)
  const list = [...events].sort((a, b) => a.seq - b.seq)
  const rows = buildTimelineRows(list, ctx)
  const stages = rows.filter(r => r.t === 'stage')
  const iters = rows.filter(r => r.t === 'iter')
  const evts = rows.filter(r => r.t === 'evt')

  // ① 零丢失、零重复 —— 分组不得吃掉或复制任何事件
  ok(evts.length === list.length, `事件零丢失零重复：${evts.length} 行 / ${list.length} 条`)
  ok(new Set(evts.map(r => r.e.id)).size === list.length, '每个事件恰好出现一次')

  // ② 时间序单调 —— 分组不能把时间线重排（这正是「不做全局聚合」的理由）
  let mono = true
  for (let i = 1; i < evts.length; i++) if (evts[i].e.seq <= evts[i - 1].e.seq) mono = false
  ok(mono, '事件仍按 seq 严格递增（分组没有打乱时间顺序）')

  // ③ 步数守恒 —— 组头的计数必须等于它实际容纳的事件数
  const stageCount = new Map()
  let cur = null
  for (const r of rows) {
    if (r.t === 'stage') { cur = r; stageCount.set(r.key, 0); continue }
    if (r.t === 'evt' && cur) stageCount.set(cur.key, stageCount.get(cur.key) + 1)
  }
  for (const s of stages) {
    ok(stageCount.get(s.key) === s.count, `阶段「${s.label}」计数自洽：${s.count} 步`)
  }
  const sum = stages.reduce((a, s) => a + s.count, 0)
  ok(sum === list.length, `各阶段步数之和 = 事件总数（${sum}）`)

  // ④ 重复出现的阶段必须标「第 N 次」，只出现一次的不标
  const segTimes = new Map()
  for (const s of stages) segTimes.set(stageOf(s.key), (segTimes.get(stageOf(s.key)) ?? 0) + 1)
  for (const s of stages) {
    const st = stageOf(s.key)
    const repeated = (segTimes.get(st) ?? 0) > 1
    const hasNth = /第 \d+ 次$/.test(s.label)
    ok(repeated === hasNth, `${s.label}｜重复出现=${repeated} 标注「第 N 次」=${hasNth}`)
  }

  // ⑤ 辅助调用（iteration 为空或 0）不得落进任何迭代组
  const wrongDepth = evts.filter(r => (hasIter(r.e) ? r.depth !== 2 : r.depth !== 1))
  ok(wrongDepth.length === 0,
    `迭代归属正确：iteration>=1 的挂迭代下、其余的挂在阶段下（异常 ${wrongDepth.length} 条）`)
  const aux = list.filter(e => !hasIter(e))
  ok(aux.every(e => evts.find(r => r.e.id === e.id).depth === 1),
    `${aux.length} 条无迭代号的事件全部不被塞进「迭代 0」`)

  // ⑥ 迭代组只应出现在编码阶段（当前数据里只有编码循环有迭代号）
  const badParent = iters.filter(r => stageOf(r.key) !== 'coding')
  ok(badParent.length === 0, `迭代组只出现在编码阶段（越界 ${badParent.length} 个）`)

  // ⑦ 迭代组与数据里的迭代号**段**一一对应。
  // 不能拿「去重排序后的迭代号」比对：一次编码阶段会跑多个 ToolCallLoop
  // （Phase 1 批量 → Phase 2 逐文件补全 → 修复循环重入），每次 run 的
  // iteration 都从 1 重新计，所以 1..5 会**重复出现多段**。
  const dataIterRuns = []
  {
    let prev = null
    for (const e of list) {
      if (!hasIter(e)) continue
      if (prev === null || e.iteration !== prev) dataIterRuns.push(e.iteration)
      prev = e.iteration
    }
  }
  const rowNums = iters.map(r => iterNumOf(r.key))
  ok(JSON.stringify(rowNums) === JSON.stringify(dataIterRuns),
    `迭代组与数据的迭代连续段逐段对齐：组 ${rowNums.length} 段 / 数据 ${dataIterRuns.length} 段`)
  const iterContentOk = rows.every((r, i) => {
    if (r.t !== 'evt' || r.depth !== 2) return true
    // 向上找最近的 iter 头
    for (let k = i; k >= 0; k--) {
      if (rows[k].t === 'iter') return iterNumOf(rows[k].key) === r.e.iteration
      if (rows[k].t === 'stage') break
    }
    return false
  })
  ok(iterContentOk, '每个挂在迭代组下的事件，其 iteration 与组号一致')

  // ⑦b **所有行的 key 全局唯一** —— 这条是需求的直接判据。
  // key 重复会同时坏两处：openState 按 key 存取（点一次「迭代 1」把同 key 的组
  // 全展开）；v-for 的 :key 重复，Vue patch 复用同 key 节点，展开内容被插到
  // 别的迭代下面。
  const allKeys = rows.map(r => r.key)
  const dupKeys = [...new Set(allKeys.filter((k, i) => allKeys.indexOf(k) !== i))]
  ok(dupKeys.length === 0,
    `全部行的 key 唯一（重复 ${dupKeys.length} 个${dupKeys.length ? '：' + dupKeys.slice(0, 5).join(', ') : ''}）`)

  // ⑦c **折叠互不串扰**：只展开「目标迭代组 + 它所属阶段」时，露出的带迭代号的
  // 事件必须恰好是目标组自己的。注意谓词必须逐 key 判断，不能写成
  // `stages.some(...)` —— 那样对任何 k 都返回 true，等于全部展开，断言会永远失败。
  if (iters.length > 1) {
    const target = iters[iters.length - 1]
    const openSet = new Set([target.key, stageKeyOf(target.key)])
    const only = buildTimelineRows(list, { ...ctx, isOpen: k => openSet.has(k) })
    const leaked = only.filter(r => r.t === 'evt' && hasIter(r.e)
      && r.e.iteration !== iterNumOf(target.key))
    ok(leaked.length === 0,
      `只展开「${target.label}」时不泄漏其它迭代的事件（泄漏 ${leaked.length} 条）`)
  }

  // ⑧ 折叠剪枝：全折叠只剩组头；只展开一段则只多出那一段的子行
  const closed = buildTimelineRows(list, { ...ctx, isOpen: () => false })
  ok(closed.every(r => r.t !== 'evt'), `全部折叠时只剩组头（${closed.length} 行，无事件行）`)
  ok(closed.length === stages.length, '全部折叠时行数 = 阶段段数')
  const firstKey = stages[0].key
  const oneOpen = buildTimelineRows(list, { ...ctx, isOpen: k => k === firstKey })
  const oneStageChildren = oneOpen.filter(r => r.t === 'evt').length
  ok(oneStageChildren === stages[0].count,
    `只展开首段时应恰好露出该段 ${stages[0].count} 步（实测 ${oneStageChildren}）`)

  // ⑨ 顺序：阶段段必须与「stage 变化 或 迭代号回退」的切分结果逐段一致。
  // 期望值要跟实现用同一把尺子（见timelineRows.ts 文件头：编码阶段一次需求里
  // 会跑多个 ToolCallLoop，每次 iteration 从 1 重来，回退即新一段），
  // 否则这条断言会把正确的切分判成错。
  const expectedSegs = []
  {
    let prevIter = null
    for (const e of list) {
      const s = e.stage ?? ''
      const it = hasIter(e) ? e.iteration : null
      const last = expectedSegs[expectedSegs.length - 1]
      const restarted = it !== null && prevIter !== null && it < prevIter
      if (last === undefined || last.s !== s || restarted) expectedSegs.push({ s })
      if (it !== null) prevIter = it
    }
  }
  const seenStages = []
  for (const r of rows) if (r.t === 'stage') seenStages.push(stageOf(r.key))
  ok(JSON.stringify(seenStages) === JSON.stringify(expectedSegs.map(s => s.s)),
    `阶段段序与时间线一致：[${seenStages.join(' → ')}]`)

  console.log(`  摘要：${stages.length} 段 / ${iters.length} 个迭代组 / ${evts.length} 条事件`)
}

console.log(`\n全部通过：${checks} 项断言`)
cleanup?.()