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

export function buildTimelineRows<E extends RowEvent>(
  items: readonly E[],
  ctx: RowContext,
): TimelineRow<E>[] {
  // 时间线的前提是时间顺序：seq 是需求内全局递增序号
  const list = [...items].sort((a, b) => a.seq - b.seq)

  // 1) 切成阶段连续段
  const segs: { stage: string; items: E[] }[] = []
  for (const e of list) {
    const st = e.stage ?? ''
    const last = segs[segs.length - 1]
    if (last && last.stage === st) last.items.push(e)
    else segs.push({ stage: st, items: [e] })
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
    let i = 0
    while (i < seg.items.length) {
      const it = seg.items[i].iteration
      if (it !== null && it !== undefined && it >= 1) {
        let j = i
        while (j < seg.items.length && seg.items[j].iteration === it) j++
        const group = seg.items.slice(i, j)
        const ikey = `${skey}@${it}`
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
