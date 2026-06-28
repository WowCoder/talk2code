<template>
  <div class="token-bar">
    <span>Tokens: <b class="tb-val">{{ tokens || '-' }}</b></span>
    <span>Input: <b class="tb-val">{{ inputTokens || '-' }}</b></span>
    <span>Output: <b class="tb-val">{{ outputTokens || '-' }}</b></span>
    <span>Cost: <b class="tb-val">{{ cost != null ? '$' + Number(cost).toFixed(4) : '-' }}</b></span>
    <span style="margin-left: auto">&#x23F1; <b class="tb-val">{{ timeMs ? (timeMs / 1000).toFixed(1) + 's' : '-' }}</b></span>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  tokens?: number
  inputTokens?: number
  outputTokens?: number
  cost?: number
  timeMs?: number
}>()
</script>

<style scoped>
.token-bar {
  padding: 8px 16px;
  background: var(--dark-surface);
  border-top: 1px solid var(--dark-border);
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 11px;
  color: var(--dark-muted);
  flex-shrink: 0;
}

.tb-val {
  color: var(--dark-fg);
  font-weight: 600;
}
</style>
