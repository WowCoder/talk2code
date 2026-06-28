<template>
  <div class="thinking-msg">
    <div class="thinking-dots">
      <span class="thinking-dot"></span>
      <span class="thinking-dot"></span>
      <span class="thinking-dot"></span>
    </div>
    <span>{{ text }}</span>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  text?: string
}>(), {
  text: '',
})
</script>

<style scoped>
.thinking-msg {
  padding: 8px 14px;
  font-size: 13px;
  color: var(--muted);
  font-style: italic;
  display: flex;
  align-items: center;
  gap: 8px;
  animation: fadeIn 0.3s;
}

.thinking-dots {
  display: flex;
  gap: 4px;
}

.thinking-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  /* 静态展示，避免历史记录中持续闪烁让人误以为还在处理 */
}
</style>
