<template>
  <div class="code-layout">
    <FileTree
      :files="fileNames"
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
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRequirementStore } from '@/stores/requirement'
import { useSettingsStore } from '@/stores/settings'
import FileTree from './FileTree.vue'
import CodeEditor from './CodeEditor.vue'

const store = useRequirementStore()
const settingsStore = useSettingsStore()

const fileNames = computed(() => Object.keys(store.codeFiles))
const activeFile = computed(() => store.activeFile)

const currentContent = computed(() => {
  if (!activeFile.value) return ''
  return store.codeFiles[activeFile.value] || ''
})

function onSelectFile(filename: string) {
  store.setActiveFile(filename)
}

function onContentChange(content: string) {
  if (!activeFile.value) return
  store.codeFiles[activeFile.value] = content
  if (settingsStore.autoSave) {
    store.saveCodeFile(activeFile.value, content)
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
