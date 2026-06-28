<template>
  <div class="file-tree">
    <div class="file-tree-header">工作区</div>
    <div class="file-tree-body">
      <div v-if="!files.length" class="file-empty">
        暂无文件
      </div>
      <div
        v-for="file in files"
        :key="file"
        :class="['tree-file', { active: file === activeFile }]"
        @click="$emit('select', file)"
      >
        <span :class="['tree-file-icon', fileExt(file)]">{{ fileLabel(file) }}</span>
        {{ file }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  files: string[]
  activeFile: string
}>()

defineEmits<{
  select: [filename: string]
}>()

function fileExt(filename: string): string {
  const ext = filename.split('.').pop() || ''
  if (['html'].includes(ext)) return 'html'
  if (['css'].includes(ext)) return 'css'
  if (['js'].includes(ext)) return 'js'
  return 'html'
}

function fileLabel(filename: string): string {
  const ext = filename.split('.').pop() || ''
  const labels: Record<string, string> = { html: 'H', css: 'C', js: 'J' }
  return labels[ext] || ext[0]?.toUpperCase() || '?'
}
</script>

<style scoped>
.file-tree {
  width: 200px;
  flex-shrink: 0;
  background: var(--dark-surface);
  border-right: 1px solid var(--dark-border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.file-tree-header {
  padding: 10px 14px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
  color: var(--dark-muted);
  text-transform: uppercase;
  border-bottom: 1px solid var(--dark-border);
  flex-shrink: 0;
}

.file-tree-body {
  flex: 1;
  overflow-y: auto;
  padding: 6px 0;
}

.file-tree-body::-webkit-scrollbar {
  width: 4px;
}

.file-tree-body::-webkit-scrollbar-thumb {
  background: oklch(35% 0.012 60);
  border-radius: 2px;
}

.file-empty {
  padding: 12px 14px;
  font-size: 12px;
  color: var(--dark-muted);
}

.tree-file {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  font-size: 13px;
  color: var(--dark-muted);
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
}

.tree-file:hover {
  color: var(--dark-fg);
  background: var(--dark-hover);
}

.tree-file.active {
  color: var(--accent);
  background: var(--dark-hover);
}

.tree-file-icon {
  width: 16px;
  height: 16px;
  border-radius: 3px;
  font-size: 9px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.tree-file-icon.html {
  background: oklch(50% 0.12 35);
  color: #fff;
}

.tree-file-icon.css {
  background: oklch(50% 0.12 260);
  color: #fff;
}

.tree-file-icon.js {
  background: oklch(50% 0.12 100);
  color: #000;
}
</style>
