/**
 * Agent 轨迹时间线的分组构建器 —— 纯函数，不依赖 Vue。
 *
 * 为什么抽出来
 * ------------
 * 分组规则是这条时间线的核心：阶段连续段切分、重复阶段标「第 N 次」、编码迭代
 * 二级分组、折叠剪枝。这些细节写错了，在界面上只会表现为「看着有点怪」，
 * 靠肉眼盯截图很难发现。抽成纯函数之后可以拿**真实事件**喂进来逐条断言
 * （见仓库根 `tmp/verify-timeline-rows.mjs`）。
 *
 * 为什么是两级、以及为什么按「连续段」而不是全局聚合
 * --------------------------------------------------
 * 一条时间线同时是「有序的」和「有层级的」，这两件事本来就是同一份数据的两个
 * 属性 —— 拆成两栏必然重复。层级取：
 *   L1 阶段（stage）：契约里的粗粒度分组 —— 规划 / 编码 / 验收 / 修复 / 交付
 *   L2 迭代（iteration）：编码循环的第 N 轮
 * 判据用 `iteration >= 1` 而不是「阶段名是不是 coding」：让数据自己说话，
 * 将来别的阶段有了迭代号也自动生效；辅助链路（规划 / 验收 / 修复）一律不带
 * 迭代号，因此不会被塞进「第 0 轮」。
 *
 * 切分必须是**连续段**：验收 → 修复 → 再验收 这种反复出现的阶段，全局聚合会
 * 排成「所有验收在前、所有修复在后」，多轮修复的因果就读不出来了。
 *
 * 连续段还要额外认一次「迭代号回退」
 * ----------------------------------
 * 编码阶段一次需求里会跑**多次** ToolCallLoop（Phase 1 批量编码 → Phase 2 逐文件
 * 定向补全 → 修复循环重入），而 `ToolCallLoop.run()` 开头就把 `iteration` 重置为 0
 * （harness/runtime.py）。这些 run 的事件 stage 全是 `coding`，在时间线上是**一个
 * 连续段**，但迭代号会走成 `1..8 → 1..5 → 1..5`。
 *
 * 如果只按 stage 切段，第二轮的「迭代 1」会拿到和第一轮一样的 key
 * （`s:coding#1@1`）—— key 重复会同时坏两处：`openState` 按 key 存取，点一次
 * 「迭代 1」会把所有同 key 的组一起展开；`v-for` 的 `:key` 重复，Vue patch 会把
 * 同 key 节点互相复用，展开内容被插到别的迭代下面（需求 220 实测 5 个 key 重复）。
 * 所以「迭代号回退」=又开了一趟编码循环，即使 stage 没变也要另起一段，
 * 由既有的「第 N 次」机制去表达，而不是新造一层概念。
 */

export interface RowEvent {
  id: number
  seq: number
  stage: string | null
  iteration: number | null
  duration_ms: number | null
}

export type TimelineRow<E extends RowEvent> =
  | { t: 'stage'; key: string; label: string; color: string; count: number; ms: number }
  | { t: 'iter'; key: string; label: string; count: number; ms: number }
  | { t: 'evt'; key: string; e: E; depth: 1 | 2 }

export interface RowContext {
  /** 该分组当前是否展开；折叠的分组只出现组头，不出现子行 */
  isOpen: (key: string) => boolean
  /** 阶段的中文名（取不到时返回空串，由本模块兜底为「未分阶段」） */
  label: (stage: string) => string
  /** 阶段配色 */
  color: (stage: string) => string
}

const sumMs = (list: readonly RowEvent[]): number =>
  list.reduce((s, e) => s + (e.duration_ms || 0), 0)

/** 参与迭代分组的迭代号（辅助链路传null/ 0，一律不进迭代组） */
const iterOf = (e: RowEvent): number | null =>
  e.iteration !== null && e.iteration !== undefined && e.iteration >= 1 ? e.iteration : null

export function buildTimelineRows<E extends RowEvent>(
  items: readonly E[],
  ctx: RowContext,
): TimelineRow<E>[] {
  // 时间线的前提是时间顺序：seq 是需求内全局递增序号
  const list = [...items].sort((a, b) => a.seq - b.seq)

  // 1) 切成阶段连续段。迭代号回退视为新的一段（新一轮编码循环，见文件头注释）
  const segs: { stage: string; items: E[] }[] = []
  let prevIter: number | null = null
  for (const e of list) {
    const st = e.stage ?? ''
    const it = iterOf(e)
    const last = segs[segs.length - 1]
    // 回退判据只看「上一条参与分组的迭代号」：段内里程碑（iteration 为空）不该
    // 把状态清成 null，否则编码收尾事件会掩盖紧随其后的迭代号回退。
    const restarted = it !== null && prevIter !== null && it < prevIter
    if (last && last.stage === st && !restarted) last.items.push(e)
    else segs.push({ stage: st, items: [e] })
    if (it !== null) prevIter = it
  }
  const segTotal = new Map<string, number>()
  for (const s of segs) segTotal.set(s.stage, (segTotal.get(s.stage) ?? 0) + 1)

  // 2) 逐段出行；折叠的分组只出行头
  const out: TimelineRow<E>[] = []
  const nthOf = new Map<string, number>()
  for (const seg of segs) {
    const nth = (nthOf.get(seg.stage) ?? 0) + 1
    nthOf.set(seg.stage, nth)
    // key 用「阶段名 + 第几次」而不是数组下标：换筛选之后下标会指向另一段，
    // 残留的折叠状态就会张冠李戴
    const skey = `s:${seg.stage}#${nth}`
    const base = ctx.label(seg.stage) || seg.stage || '未分阶段'
    const times = segTotal.get(seg.stage) ?? 1
    out.push({
      t: 'stage',
      key: skey,
      label: times > 1 ? `${base} · 第 ${nth} 次` : base,
      color: ctx.color(seg.stage),
      count: seg.items.length,
      ms: sumMs(seg.items),
    })
    if (!ctx.isOpen(skey)) continue

    // 3) 阶段内按迭代号再切段
    // 同一迭代号在段内理论上只出现一次（回退已被上面切成新段），但段内里程碑
    // （iteration 为空）夹在两段同号迭代之间时会绕开回退判据，产生同 key 的第二个
    // 组。occurrence 计数兜住这种情况：真出现时第二个 key 加后缀，绝不让 key 重复。
    const iterSeen = new Map<number, number>()
    let i = 0
    while (i < seg.items.length) {
      const it = seg.items[i].iteration
      if (it !== null && it !== undefined && it >= 1) {
        let j = i
        while (j < seg.items.length && seg.items[j].iteration === it) j++
        const group = seg.items.slice(i, j)
        const occ = (iterSeen.get(it) ?? 0) + 1
        iterSeen.set(it, occ)
        const ikey = occ > 1 ? `${skey}@${it}~${occ}` : `${skey}@${it}`
        out.push({
          t: 'iter', key: ikey, label: `迭代 ${it}`,
          count: group.length, ms: sumMs(group),
        })
        if (ctx.isOpen(ikey)) {
          for (const e of group) out.push({ t: 'evt', key: `e${e.id}`, e, depth: 2 })
        }
        i = j
      } else {
        // 阶段内不参与迭代的事件（如「编码收尾」里程碑）直接挂在阶段下
        out.push({ t: 'evt', key: `e${seg.items[i].id}`, e: seg.items[i], depth: 1 })
        i++
      }
    }
  }
  return out
}
