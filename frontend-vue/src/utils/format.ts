/**
 * 后台页共用的格式化。**唯一实现处** —— 同一份耗时/时间在两个页面上
 * 各写一遍，就会出现「一边 1m 30s、一边 90s」这类不一致。
 *
 * 只服务后台的轨迹相关页面（需求轨迹 / 评测）；前台的展示口径不同，
 * 不强行合并。
 */

export function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/**
 * 毫秒 → 12s / 1m 30s / 1h 5m。
 * 0 与空值都显示 —：「没记录到耗时」不能冒充「0 秒」。
 */
export function fmtDuration(ms: number | null | undefined): string {
  if (!ms) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`
}

/** 09-30 13:12 —— 列表与切换器上用，精度到分钟足够 */
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 13:12:04 —— 时间线行上用，同一条链路的先后顺序要精确到秒 */
export function fmtClock(iso: string | null | undefined): string {
  if (!iso) return '--:--:--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--:--:--'
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 2026-09-30 13:12:04 —— 同一天提交的多个对象只到分钟分不出先后 */
export function fmtFull(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
    + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 计数分档：token 量级跨好几个数量级，单位写死任一个都不好读 */
export function fmtCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  return n >= 10000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}
