<template>
  <div class="empty-state">
    <div class="empty-icon">
      <svg viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="var(--muted)" stroke-width="1.5" stroke-linecap="round">
        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
      </svg>
    </div>
    <p class="empty-text">{{ text }}</p>
    <router-link v-if="showCreate" to="/" class="empty-link">开始创建</router-link>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  text?: string
  showCreate?: boolean
}>(), {
  text: '暂无项目',
  showCreate: true,
})
</script>

<style scoped>
.empty-state {
  text-align: center;
  padding: 60px 20px;
}

.empty-icon {
  margin-bottom: 16px;
  opacity: 0.5;
}

.empty-text {
  font-size: 14px;
  color: var(--muted);
  margin-bottom: 16px;
}

.empty-link {
  display: inline-block;
  padding: 8px 20px;
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 13px;
  color: var(--accent);
  transition: background 0.15s;
}

.empty-link:hover {
  background: var(--accent-soft);
}
</style>
