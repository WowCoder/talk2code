<template>
  <div class="tab-switcher">
    <button
      :class="['tab', { active: activeTab === 'login' }]"
      @click="$emit('update:activeTab', 'login')"
    >
      登录
    </button>
    <button
      :class="['tab', { active: activeTab === 'register' }]"
      @click="$emit('update:activeTab', 'register')"
    >
      注册
    </button>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  activeTab: 'login' | 'register'
}>()

defineEmits<{
  'update:activeTab': [value: 'login' | 'register']
}>()
</script>

<style scoped>
.tab-switcher {
  display: flex;
  gap: 0;
  margin-bottom: 24px;
  background: var(--bg);
  border-radius: 10px;
  padding: 3px;
}

.tab {
  flex: 1;
  padding: 8px 0;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  font-family: var(--font-body);
  color: var(--muted);
  cursor: pointer;
  background: transparent;
  transition: all 0.2s;
}

.tab.active {
  background: var(--surface);
  color: var(--accent);
  font-weight: 600;
}
</style>
