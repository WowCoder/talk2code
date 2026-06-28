<template>
  <div class="hook-card">
    <div :class="['hk-icon', passed ? 'check' : 'error']">
      {{ passed ? '✅' : '⚠️' }}
    </div>
    <div class="hk-body">
      <div class="hk-title">{{ hookName }}</div>
      <div v-if="message" class="hk-summary">{{ message }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  hookName: string
  passed: boolean
  message?: string
}>()
</script>

<style scoped>
.hook-card {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 8px 12px;
  margin: 4px 0;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.hk-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  flex-shrink: 0;
}

.hk-icon.check {
  background: oklch(90% 0.05 155);
}

.hk-icon.error {
  background: oklch(92% 0.03 20);
}

.hk-body {
  flex: 1;
  min-width: 0;
}

.hk-title {
  font-weight: 600;
  color: var(--fg);
}

.hk-summary {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
}
</style>
