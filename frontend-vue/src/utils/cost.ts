/**
 * 成本的展示口径 —— 全站唯一实现。
 *
 * 数值来自后端 `cost_tracker`（backend/harness/observability/cost.py），按模型
 * 刊例价累加得出，统一展示估算成本 USD，不做汇率折算。
 *
 * 精度由调用方按金额量级决定，不写死：
 *   - 单条 trace / 单次生成：4 位（金额常在小数点后 3~4 位，2 位会塌成 0.00）
 *   - 7 日累计等聚合值：2 位
 *
 * 微小金额自动提高精度；空值与真实零分别显示占位符和 $0。
 */

export const COST_SYMBOL = '$'
export const COST_CURRENCY = 'USD'

export function formatCost(v: number | null | undefined, digits = 4): string {
  if (v == null || !Number.isFinite(v)) return '—'
  if (v === 0) return `${COST_SYMBOL}0`
  const amount = Math.abs(v)
  const fixed = amount.toFixed(digits)
  const text = Number(fixed) === 0 ? amount.toPrecision(2) : fixed
  return `${v < 0 ? '-' : ''}${COST_SYMBOL}${text}`
}

/** 没有 usage 的调用无法证明零成本；仍保留已知调用的部分估算。 */
export function reportedCost(
  cost: number | null | undefined,
  usageReportedCalls?: number,
  llmCalls?: number,
): number | null {
  if (cost == null || !Number.isFinite(cost)) return null
  if (cost === 0 && usageReportedCalls === 0 && (llmCalls ?? 0) > 0) return null
  return cost
}

/** CSV 列头声明 USD，值保持可求和的原始数值，不四舍五入微小成本。 */
export function costCsvValue(cost: number | null | undefined): string {
  return cost == null || !Number.isFinite(cost) ? '' : String(cost)
}
