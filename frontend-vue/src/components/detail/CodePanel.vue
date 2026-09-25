<template>
  <div class="code-layout">
    <template v-if="hasFiles">
      <FileTree
        :files="fileTree"
        :active-file="activeFile"
        @select="onSelectFile"
      />
      <div class="code-main">
        <div class="code-filename">{{ activeFile || '--' }}</div>
        <CodeEditor
          :content="currentContent"
          :filename="activeFile || ''"
          :font-size="settingsStore.codeFontSize"
          @update:content="onContentChange"
        />
      </div>
    </template>
    <!-- 无产物时空树 + 空编辑器等于一片白，用户看不出是还在生成还是已经失败了 -->
    <div v-else class="code-empty">
      <p class="code-empty-title">{{ emptyTitle }}</p>
      <p class="code-empty-hint">{{ emptyHint }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRequirementStore } from '@/stores/requirement'
import { useSettingsStore } from '@/stores/settings'
import { useToast } from '@/composables/useToast'
import { useArtifactEmptyState } from '@/composables/useArtifactEmptyState'
import FileTree from './FileTree.vue'
import type { TreeNode } from './FileTree.vue'
import CodeEditor from './CodeEditor.vue'

const store = useRequirementStore()
const settingsStore = useSettingsStore()
const { show } = useToast()

// 空态文案与预览 TAB 共用一套（useArtifactEmptyState），避免两处说法不一致
const { hasFiles, title: emptyTitle, hint: emptyHint } = useArtifactEmptyState()

const fileTree = computed<TreeNode[]>(() => buildTree(Object.keys(store.codeFiles)))
const activeFile = computed(() => store.activeFile)

const currentContent = computed(() => {
  if (!activeFile.value) return ''
  return store.codeFiles[activeFile.value] || ''
})

function onSelectFile(filename: string) {
  store.setActiveFile(filename)
}

// Build a nested directory tree from flat "dir/file.ext" path keys.
function buildTree(paths: string[]): TreeNode[] {
  const root: TreeNode = { name: '', path: '', type: 'folder', children: [] }
  for (const full of paths) {
    const parts = full.split('/').filter(Boolean)
    if (!parts.length) continue
    let node = root
    let cur = ''
    parts.forEach((part, i) => {
      cur = cur ? `${cur}/${part}` : part
      const isFile = i === parts.length - 1
      let child = node.children.find((c) => c.name === part)
      if (!child) {
        child = { name: part, path: cur, type: isFile ? 'file' : 'folder', children: [] }
        node.children.push(child)
      } else if (!isFile && child.type === 'file') {
        // A segment previously seen as a file is actually a directory.
        child.type = 'folder'
      }
      node = child
    })
  }
  sortNodes(root.children)
  return root.children
}

function sortNodes(nodes: TreeNode[]) {
  nodes.sort((a, b) => {
    if (a.type !== b.type) return a.type === 'folder' ? -1 : 1
    return a.name.localeCompare(b.name)
  })
  nodes.forEach((n) => sortNodes(n.children))
}

function onContentChange(content: string) {
  if (!activeFile.value) return
  store.codeFiles[activeFile.value] = content
  if (settingsStore.autoSave) {
    store.saveCodeFile(activeFile.value, content).catch((err: any) => {
      show('保存失败: ' + (err.message || '未知错误'), 'error')
    })
  }
}
</script>

<style scoped>
.code-layout {
  display: flex;
  flex: 1;
  min-height: 0;
}

.code-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.code-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 24px;
  text-align: center;
}

.code-empty-title {
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--fg);
}

.code-empty-hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--muted);
  max-width: 320px;
}

.code-filename {
  padding: 8px 16px;
  font-size: 12px;
  color: var(--dark-muted);
  background: var(--dark-surface);
  border-bottom: 1px solid var(--dark-border);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
