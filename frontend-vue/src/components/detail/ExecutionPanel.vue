<template>
  <div v-if="traceData" class="exec-panel">
    <div class="exec-panel-header" @click="expanded = !expanded">
      📊 执行详情
      <span>
        {{ spanCount }} 步 · {{ (totalDuration / 1000).toFixed(1) }}s ·
        {{ totalTokens }} tokens · ${{ totalCost.toFixed(4) }}
        {{ expanded ? '▾' : '▸' }}
      </span>
    </div>
    <div :class="['exec-panel-body', { open: expanded }]">
      <div
        v-for="(span, i) in (traceData.spans || [])"
        :key="i"
        :class="['exec-row', span.status]"
      >
        <span :class="['exec-dot', span.status || 'running']"></span>
        {{ span.name }}
        <span style="margin-left: auto">
          {{ span.duration_ms ? span.duration_ms + 'ms' : '进行中' }}
        </span>
      </div>
      <div v-if="totalTokens" class="exec-row" style="font-weight:600;border-top:1px solid var(--dark-border);padding-top:4px;margin-top:4px">
        Total: {{ totalTokens }} tokens · Cost: ${{ totalCost.toFixed(4) }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { SSETraceSummaryData } from '@/types/sse'

const props = defineProps<{
  traceData: SSETraceSummaryData | null
}>()

const expanded = ref(false)

const spanCount = computed(() => props.traceData?.spans?.length || props.traceData?.span_count || 0)
const totalDuration = computed(() => props.traceData?.total_duration_ms || 0)
const totalTokens = computed(() => props.traceData?.total_tokens || 0)
const totalCost = computed(() => props.traceData?.total_cost || 0)
</script>

<style scoped>
.exec-panel {
  background: var(--dark-surface);
  border: 1px solid var(--dark-border);
  border-radius: 8px;
  margin: 4px 0;
  overflow: hidden;
  align-self: flex-start;
  width: 100%;
}

.exec-panel-header {
  padding: 8px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  font-size: 12px;
  color: var(--dark-muted);
  user-select: none;
}

.exec-panel-header:hover {
  color: var(--dark-fg);
}

.exec-panel-body {
  padding: 8px 12px;
  font-size: 11px;
  display: none;
}

.exec-panel-body.open {
  display: block;
}

.exec-row {
  display: flex;
  align-items: center;
  padding: 3px 0;
  color: var(--dark-muted);
  font-size: 11px;
  gap: 8px;
}

.exec-row.success {
  color: oklch(65% 0.08 155);
}

.exec-row.failure {
  color: oklch(62% 0.12 20);
}

.exec-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.exec-dot.success {
  background: oklch(60% 0.08 155);
}

.exec-dot.failure {
  background: oklch(55% 0.12 20);
}

.exec-dot.running {
  background: var(--accent);
}
</style>
