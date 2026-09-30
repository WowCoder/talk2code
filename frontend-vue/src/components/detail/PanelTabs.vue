<template>
  <div class="panel-tabbar">
    <div class="panel-tabs" role="tablist" aria-label="工作台视图切换">
      <button
        :class="['panel-tab', { active: activeTab === 'spec' }]"
        role="tab"
        :aria-selected="activeTab === 'spec'"
        @click="$emit('update:activeTab', 'spec')"
      >
        方案 Spec
      </button>
      <button
        :class="['panel-tab', { active: activeTab === 'tasks' }]"
        role="tab"
        :aria-selected="activeTab === 'tasks'"
        @click="$emit('update:activeTab', 'tasks')"
      >
        任务
      </button>
      <button
        :class="['panel-tab', { active: activeTab === 'preview' }]"
        role="tab"
        :aria-selected="activeTab === 'preview'"
        @click="$emit('update:activeTab', 'preview')"
      >
        预览
      </button>
      <button
        :class="['panel-tab', { active: activeTab === 'code' }]"
        role="tab"
        :aria-selected="activeTab === 'code'"
        @click="$emit('update:activeTab', 'code')"
      >
        代码
      </button>
      <button
        :class="['panel-tab', { active: activeTab === 'publish' }]"
        role="tab"
        :aria-selected="activeTab === 'publish'"
        @click="$emit('update:activeTab', 'publish')"
      >
        发布
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  activeTab: string
}>()

defineEmits<{
  'update:activeTab': [value: string]
}>()
</script>

<style scoped>
.panel-tabbar {
  display: flex;
  align-items: center;
  padding: 8px 16px 0;
  background: var(--wb-bg);
  border-bottom: 1px solid var(--wb-border);
  flex-shrink: 0;
}

.panel-tabs {
  display: flex;
  gap: 4px;
}

.panel-tab {
  padding: 7px 14px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--wb-muted);
  cursor: pointer;
  border: none;
  background: none;
  font-family: var(--font-body);
  border-radius: 8px 8px 0 0;
  transition: color 0.15s, background 0.15s;
}

.panel-tab:hover {
  color: var(--wb-fg);
  background: var(--wb-hover);
}

.panel-tab.active {
  color: var(--wb-fg);
  background: var(--wb-elevated);
  font-weight: 600;
}
</style>
