<template>
  <div class="file-tree">
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
  /* 不再固定 220px：外层 .code-side 是 208px，固定宽会横向溢出，
     溢出会让侧栏凭空多出一条拖分栏也消不掉的滚动条 */
  width: 100%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  /* 不自建滚动容器：滚动统一交给外层 .code-side，避免双滚动条 */
  overflow: visible;
}

.file-tree-body {
  padding: 6px 0;
}

.file-empty {
  padding: 12px 14px;
  font-size: 12px;
  color: var(--dark-muted);
}
</style>
