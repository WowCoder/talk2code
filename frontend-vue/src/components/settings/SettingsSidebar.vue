<template>
  <nav class="sidebar">
    <h3 class="sidebar-title">设置</h3>
    <button
      v-for="section in sections"
      :key="section.key"
      :class="['sidebar-link', { active: activeSection === section.key }]"
      @click="$emit('update:activeSection', section.key)"
    >
      {{ section.label }}
    </button>
  </nav>
</template>

<script setup lang="ts">
defineProps<{
  activeSection: string
}>()

defineEmits<{
  'update:activeSection': [value: string]
}>()

const sections = [
  { key: 'profile', label: '个人资料' },
  { key: 'appearance', label: '外观偏好' },
  { key: 'account', label: '账户安全' },
  { key: 'about', label: '关于' },
]
</script>

<style scoped>
.sidebar {
  width: 180px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sidebar-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 0 12px 12px;
}

.sidebar-link {
  padding: 10px 12px;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-family: var(--font-body);
  color: var(--muted);
  cursor: pointer;
  background: transparent;
  text-align: left;
  transition: all 0.15s;
}

.sidebar-link:hover {
  color: var(--fg);
  background: var(--bg);
}

.sidebar-link.active {
  color: var(--accent);
  background: var(--accent-soft);
  font-weight: 600;
}
</style>
