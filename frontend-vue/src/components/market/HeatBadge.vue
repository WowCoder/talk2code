<template>
  <span class="heat" :title="`热度 ${value}`">
    <span class="heat-flame"></span>
    <span class="heat-num">{{ value }}</span>
  </span>
</template>

<script setup lang="ts">
// 热度是后端算好的派生量（去重访客 + 点赞加权 + 时间衰减）。
// 前端只负责取整显示，不参与计算 —— 排序与权重必须只有一份实现。
const props = defineProps<{ heat: number }>()
const value = Math.max(1, Math.round(props.heat || 0))
</script>

<style scoped>
.heat {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px 2px 6px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  line-height: 1.6;
  font-variant-numeric: tabular-nums;
}

.heat-flame {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  background: var(--accent);
  opacity: 0.85;
}

.heat-num {
  font-weight: 600;
}
</style>
