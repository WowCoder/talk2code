<template>
  <div class="token-bar">
    <span>tokens <b class="tb-val">{{ tokens ? tokens.toLocaleString() : '-' }}</b></span>
    <span>cost <b class="tb-val">{{ cost != null ? '$' + Number(cost).toFixed(2) : '-' }}</b></span>
    <span>elapsed <b class="tb-val">{{ elapsedText }}</b></span>
    <span class="tb-right">由 LangGraph 驱动</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  tokens?: number
  inputTokens?: number
  outputTokens?: number
  cost?: number
  timeMs?: number
}>()

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
  align-items: center;
  gap: 18px;
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
</style>
