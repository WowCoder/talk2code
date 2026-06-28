<template>
  <div class="progress">
    <div class="progress-fill" :style="{ width: percent + '%' }"></div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  percent: number
}>()
</script>

<style scoped>
.progress {
  height: 3px;
  background: var(--dark-border);
  flex-shrink: 0;
}

.progress-fill {
  height: 100%;
  background: var(--accent);
  width: 0;
  transition: width 0.3s;
}
</style>
