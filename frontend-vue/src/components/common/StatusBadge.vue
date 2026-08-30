<template>
  <span :class="['status-badge', status]">
    {{ label }}
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  status: 'pending' | 'processing' | 'planning' | 'finished' | 'finished_with_issues' | 'needs_user_input' | 'failed'
}>()

const labels: Record<string, string> = {
  pending: '等待中',
  processing: '处理中',
  planning: '待确认',
  finished: '已完成',
  finished_with_issues: '已完成 (有问题)',
  needs_user_input: '待用户处理',
  failed: '失败',
}

const label = computed(() => labels[props.status] || props.status)
</script>

<style scoped>
.status-badge {
  padding: 3px 10px;
  border-radius: 100px;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
}

.status-badge.finished {
  background: oklch(90% 0.06 155);
  color: oklch(40% 0.12 155);
}

.status-badge.processing {
  background: oklch(90% 0.04 250);
  color: oklch(45% 0.12 250);
}

.status-badge.pending {
  background: oklch(90% 0.04 85);
  color: oklch(45% 0.1 85);
}

.status-badge.failed {
  background: oklch(92% 0.03 20);
  color: oklch(45% 0.15 20);
}

.status-badge.finished_with_issues {
  background: oklch(92% 0.04 65);
  color: oklch(45% 0.14 55);
}

.status-badge.needs_user_input {
  background: oklch(92% 0.05 30);
  color: oklch(45% 0.16 25);
}

.status-badge.planning {
  background: oklch(90% 0.04 250);
  color: oklch(45% 0.12 250);
}
</style>
