/**
 * 成本的展示口径 —— 全站唯一实现。
 *
 * 数值来自后端 `cost_tracker`（backend/harness/observability/cost.py），按模型
 * 刊例价累加得出，价格表以美元计价；展示统一用 ¥ 符号，不做汇率折算 —— 这是
 * 内部观测口径，关心的是量级与相对变化，不是对账。
 *
 * 精度由调用方按金额量级决定，不写死：
 *   - 单条 trace / 单次生成：4 位（金额常在小数点后 3~4 位，2 位会塌成 0.00）
 *   - 7 日累计等聚合值：2 位
 *
 * 空值（null / undefined / NaN）与「花了 0 元」是两件事，前者出占位符，后者出 ¥0。
 */

export const COST_SYMBOL = '¥'

export function formatCost(v: number | null | undefined, digits = 4): string {
  if (v == null || !Number.isFinite(v)) return '—'
  // 0 不带尾随小数：¥0.0000 那几位没有任何信息量
  if (v === 0) return `${COST_SYMBOL}0`
  return `${COST_SYMBOL}${v.toFixed(digits)}`
}
