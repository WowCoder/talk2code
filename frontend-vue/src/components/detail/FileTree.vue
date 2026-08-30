<template>
  <div class="file-tree">
    <div class="file-tree-header">工作区</div>
    <div class="file-tree-body">
      <div v-if="!files.length" class="file-empty">
        暂无文件
      </div>
      <FileTreeNode
        v-for="node in files"
        :key="node.path"
        :node="node"
        :depth="0"
        :active-file="activeFile"
        @select="$emit('select', $event)"
      />
    </div>
  </div>
</template>

<script lang="ts">
export interface TreeNode {
  name: string
  path: string
  type: 'file' | 'folder'
  children: TreeNode[]
}
</script>

<script setup lang="ts">
import FileTreeNode from './FileTreeNode.vue'

defineProps<{
  files: TreeNode[]
  activeFile: string
}>()

defineEmits<{
  select: [filename: string]
}>()
</script>

<style scoped>
.file-tree {
  width: 220px;
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
</style>
