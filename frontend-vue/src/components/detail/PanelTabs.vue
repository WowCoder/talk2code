<template>
  <div class="panel-tabbar">
    <div class="panel-tabs">
      <button
        :class="['panel-tab', { active: activeTab === 'spec' }]"
        @click="$emit('update:activeTab', 'spec')"
      >
        📋 Spec
      </button>
      <button
        :class="['panel-tab', { active: activeTab === 'tasks' }]"
        @click="$emit('update:activeTab', 'tasks')"
      >
        📝 任务
      </button>
      <button
        :class="['panel-tab', { active: activeTab === 'preview' }]"
        @click="$emit('update:activeTab', 'preview')"
      >
        👁 预览
      </button>
      <button
        :class="['panel-tab', { active: activeTab === 'code' }]"
        @click="$emit('update:activeTab', 'code')"
      >
        📄 代码
      </button>
    </div>
    <button class="btn-download" @click="$emit('download')">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
        <polyline points="7 10 12 15 17 10"/>
        <line x1="12" y1="15" x2="12" y2="3"/>
      </svg>
      代码下载
    </button>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  activeTab: string
}>()

defineEmits<{
  'update:activeTab': [value: string]
  download: []
}>()
</script>

<style scoped>
.panel-tabbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  background: var(--dark-surface);
  border-bottom: 1px solid var(--dark-border);
  flex-shrink: 0;
}

.panel-tabs {
  display: flex;
  gap: 0;
}

.panel-tab {
  padding: 10px 18px;
  font-size: 13px;
  font-weight: 500;
  color: var(--dark-muted);
  cursor: pointer;
  border: none;
  background: none;
  font-family: var(--font-body);
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color 0.15s;
}

.panel-tab:hover {
  color: var(--dark-fg);
}

.panel-tab.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

.btn-download {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  font-family: var(--font-body);
  color: #fff;
  cursor: pointer;
  border: none;
  background: var(--accent);
  transition: background 0.2s;
}

.btn-download:hover {
  background: oklch(58% 0.13 28);
}
</style>
