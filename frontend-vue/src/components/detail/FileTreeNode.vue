<template>
  <div class="tree-branch">
    <!-- Folder -->
    <div
      v-if="node.type === 'folder'"
      class="tree-row tree-folder"
      :class="{ active: isActive }"
      :style="{ paddingLeft: indent + 'px' }"
      @click="toggle"
    >
      <span class="tree-chevron" :class="{ open: expanded }">
        <svg viewBox="0 0 16 16" width="12" height="12">
          <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </span>
      <span class="tree-folder-icon">
        <svg viewBox="0 0 16 16" width="16" height="16">
          <path d="M1.5 4a1 1 0 0 1 1-1h3.2a1 1 0 0 1 .8.4L7.8 4.6H13a1 1 0 0 1 1 1V12a1 1 0 0 1-1 1H2.5a1 1 0 0 1-1-1V4z" fill="currentColor" />
        </svg>
      </span>
      <span class="tree-label">{{ node.name }}</span>
    </div>

    <!-- File -->
    <div
      v-else
      class="tree-row tree-file"
      :class="{ active: isActive }"
      :style="{ paddingLeft: indent + 'px' }"
      @click="$emit('select', node.path)"
    >
      <span class="tree-chevron-spacer"></span>
      <span :class="['tree-file-icon', fileExt(node.name)]">{{ fileLabel(node.name) }}</span>
      <span class="tree-label">{{ node.name }}</span>
    </div>

    <!-- Children -->
    <div v-if="node.type === 'folder' && expanded" class="tree-children">
      <FileTreeNode
        v-for="child in node.children"
        :key="child.path"
        :node="child"
        :depth="depth + 1"
        :active-file="activeFile"
        @select="$emit('select', $event)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { TreeNode } from './FileTree.vue'

const props = defineProps<{
  node: TreeNode
  depth: number
  activeFile: string
}>()

const emit = defineEmits<{
  select: [filename: string]
}>()

// Folders are expanded by default so the hierarchy is visible at a glance.
const expanded = ref(true)

const indent = computed(() => props.depth * 12 + 8)

const isActive = computed(() => {
  if (props.node.type === 'file') return props.node.path === props.activeFile
  return props.activeFile.startsWith(props.node.path + '/')
})

function toggle() {
  expanded.value = !expanded.value
}

function fileExt(filename: string): string {
  const ext = filename.split('.').pop() || ''
  if (['html', 'htm'].includes(ext)) return 'html'
  if (['css'].includes(ext)) return 'css'
  if (['js', 'mjs'].includes(ext)) return 'js'
  if (['md'].includes(ext)) return 'md'
  return 'html'
}

function fileLabel(filename: string): string {
  const ext = filename.split('.').pop() || ''
  const labels: Record<string, string> = { html: 'H', htm: 'H', css: 'C', js: 'J', mjs: 'J', md: 'M' }
  return labels[ext] || ext[0]?.toUpperCase() || '?'
}
</script>

<style scoped>
.tree-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 26px;
  padding: 0 8px;
  font-size: 13px;
  color: var(--dark-muted);
  cursor: pointer;
  user-select: none;
  transition: color 0.12s, background 0.12s;
}

.tree-row:hover {
  color: var(--dark-fg);
  background: var(--dark-hover);
}

.tree-file.active {
  color: var(--accent);
  background: var(--dark-hover);
}

.tree-chevron {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--dark-muted);
  transition: transform 0.12s ease;
}

.tree-chevron.open {
  transform: rotate(90deg);
}

.tree-chevron-spacer {
  width: 12px;
  flex-shrink: 0;
}

.tree-folder-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: oklch(68% 0.07 250);
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

.tree-file-icon.md {
  background: oklch(50% 0.08 260);
  color: #fff;
}

.tree-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
