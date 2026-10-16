import type { UsageAccounting } from '../types/usage'
import type { SSETraceSummaryData } from '../types/sse'

export function formatUsagePercent(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '未上报' : `${(value * 100).toFixed(1)}%`
}

export function cacheUsageTitle(usage: UsageAccounting): string {
  const count = (n?: number) => n == null ? '—' : n.toLocaleString('en-US')
  return `缓存命中 ${count(usage.cached_tokens)} / ${count(usage.cache_reported_tokens_in)} 个已上报缓存的输入 token；`
    + `输入覆盖率 ${formatUsagePercent(usage.cache_coverage)}（${count(usage.cache_reported_tokens_in)} / ${count(usage.tokens_in)}）；`
    + `${count(usage.cache_reported_calls)} 次调用上报缓存信息`
}

export function cacheSavingsTitle(usage: UsageAccounting): string {
  return `${usage.cache_savings_reported_calls ?? '—'} / ${usage.llm_calls ?? '—'} 次调用具有已知缓存净节省；仅汇总已知样本，未知样本未计入`
}

export function selectRequirementUsage(
  summary: UsageAccounting | null | undefined,
  trace: SSETraceSummaryData | null | undefined,
): { scope: string; usage: UsageAccounting } {
  if (summary) return { scope: '需求累计', usage: summary }
  return {
    scope: '本次执行',
    usage: { ...trace, tokens: trace?.total_tokens, cost: trace?.total_cost, tokens_in: trace?.input_tokens },
  }
}
