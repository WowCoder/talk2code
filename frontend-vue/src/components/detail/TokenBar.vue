<template>
  <div class="token-bar">
    <span>{{ scope }}<template v-if="summaryStale"> · 快照待刷新</template></span>
    <span>tokens <b class="tb-val">{{ tokensText }}</b></span>
    <span>估算成本 USD <b class="tb-val">{{ formatCost(reportedCost(cost, usageReportedCalls, llmCalls)) }}</b></span>
    <span class="tb-cache" :title="cacheTitle">
      缓存命中 <b class="tb-val">{{ formatUsagePercent(cacheHitRate) }}</b>
      · 输入覆盖率 <b class="tb-val">{{ formatUsagePercent(cacheCoverage) }}</b>
    </span>
    <span :title="`${usageReportedCalls ?? '—'} / ${llmCalls ?? '—'} 次调用上报 usage`">
      usage 覆盖率 <b class="tb-val">{{ formatUsagePercent(usageCoverage) }}</b>
    </span>
    <span v-if="costFallbackCalls" class="tb-warning">{{ costFallbackCalls }} 次调用按兜底价估算</span>
    <span v-if="cacheSavings != null" :title="cacheSavingsTitle({ cache_savings_reported_calls: cacheSavingsReportedCalls, llm_calls: llmCalls })">已知样本缓存净节省 USD <b class="tb-val">{{ formatCost(cacheSavings) }}</b></span>
    <span class="tb-right">由 LangGraph 驱动</span>
    <span class="tb-execution">
      <template v-if="scope === '需求累计'">
        本次执行快照 {{ executionTokens == null ? '—' : executionTokens.toLocaleString() }} tokens
        · 估算成本 USD {{ formatCost(executionCost) }} ·
      </template>
      本次耗时 {{ elapsedText }}
      <template v-if="summaryStale"> · 累计包含已记录用量，执行结束后自动刷新</template>
    </span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatCost, reportedCost } from '@/utils/cost'
import { cacheSavingsTitle, cacheUsageTitle, formatUsagePercent } from '@/utils/usageDisplay'

const props = defineProps<{
  tokens?: number
  inputTokens?: number
  outputTokens?: number
  cost?: number | null
  timeMs?: number
  cachedTokens?: number
  scope?: string
  summaryStale?: boolean
  executionTokens?: number
  executionCost?: number | null
  /** 0~1；null / undefined = 无上报数据 */
  cacheHitRate?: number | null
  cacheCoverage?: number | null
  cacheReportedTokensIn?: number
  cacheReportedCalls?: number
  usageReportedCalls?: number
  usageCoverage?: number | null
  llmCalls?: number
  costFallbackCalls?: number
  cacheSavings?: number | null
  cacheSavingsReportedCalls?: number
}>()

const cacheTitle = computed(() => cacheUsageTitle({
  cached_tokens: props.cachedTokens, tokens_in: props.inputTokens,
  cache_reported_tokens_in: props.cacheReportedTokensIn,
  cache_reported_calls: props.cacheReportedCalls, cache_coverage: props.cacheCoverage,
}))
const tokensText = computed(() => {
  if (props.tokens == null || (props.tokens === 0 && props.usageReportedCalls === 0 && (props.llmCalls ?? 0) > 0)) return '—'
  return props.tokens.toLocaleString()
})

const elapsedText = computed(() => {
  if (!props.timeMs) return '-'
  const totalSec = Math.floor(props.timeMs / 1000)
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  return m > 0 ? `${m}m ${s}s` : `${s}s`
})
</script>

<style scoped>
.token-bar {
  padding: 7px 16px;
  background: var(--wb-surface);
  border-top: 1px solid var(--wb-border);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 18px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--wb-faint);
  flex-shrink: 0;
}

.tb-val {
  color: var(--wb-fg);
  font-weight: 500;
}

.tb-right {
  margin-left: auto;
  color: var(--wb-faint);
}
.tb-execution { flex-basis: 100%; }
.tb-warning { color: var(--color-warning); }
</style>
