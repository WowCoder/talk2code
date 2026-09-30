<template>
  <div class="code-layout">
    <template v-if="hasFiles">
      <div class="code-body">
        <div class="code-side">
          <div class="code-side-label">EXPLORER</div>
          <FileTree
            :files="fileTree"
            :active-file="activeFile"
            @select="onSelectFile"
          />
        </div>
        <div class="code-main">
          <!-- 下载 / 复制收敛到代码工作台同一栏：其它 Tab 用不到这两个动作 -->
          <div class="editor-bar">
            <span class="editor-file">{{ activeFile || '--' }}</span>
            <div class="editor-actions">
              <button class="bar-btn ghost" @click="$emit('download')">
                <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                  <polyline points="7 10 12 15 17 10"/>
                  <line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
                下载代码
              </button>
              <button class="bar-btn solid" @click="copyAll">
                <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2"/>
                  <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
                </svg>
                复制全部
              </button>
            </div>
          </div>
          <CodeEditor
            :content="currentContent"
            :filename="activeFile || ''"
            :font-size="settingsStore.codeFontSize"
            @update:content="onContentChange"
          />
        </div>
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

const emit = defineEmits<{
  download: []
}>()

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

/** 复制全部文件（路径 + 内容），方便贴给别处继续改 */
async function copyAll() {
  const entries = Object.entries(store.codeFiles)
  if (!entries.length) {
    show('没有可复制的代码', 'error')
    return
  }
  const text = entries
    .map(([path, content]) => `// ===== ${path} =====\n${content}`)
    .join('\n\n')
  try {
    await navigator.clipboard.writeText(text)
    show(`已复制 ${entries.length} 个文件`, 'success')
  } catch {
    show('复制失败：浏览器未授权剪贴板', 'error')
  }
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
  flex-direction: column;
  flex: 1;
  min-height: 0;
  background: var(--wb-bg);
}

.code-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.code-side {
  width: 208px;
  flex-shrink: 0;
  border-right: 1px solid var(--wb-border);
  background: var(--wb-surface);
  overflow-y: auto;
  overflow-x: hidden;
  padding: 8px 0 16px;
}

/* 侧栏唯一滚动条：细条 + 跟随主题，文件不多时不会出现 */
.code-side::-webkit-scrollbar {
  width: 4px;
}

.code-side::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--wb-faint) 55%, transparent);
  border-radius: 2px;
}

.code-side::-webkit-scrollbar-track {
  background: transparent;
}

.code-side-label {
  padding: 8px 16px 6px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.14em;
  color: var(--wb-faint);
}

.code-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

/* 编辑器占满顶栏以下的空间并自行滚动；
   height:100% 会把顶栏高度也撑进去造成 1px 溢出 → 外层出滚动条 */
.code-main :deep(.code-editor-container) {
  flex: 1;
  min-height: 0;
  height: auto;
}

/* 编辑器顶栏：当前文件 + 下载 / 复制（设计稿同一栏） */
.editor-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  background: var(--wb-bg);
  border-bottom: 1px solid var(--wb-border);
  flex-shrink: 0;
}

.editor-file {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--wb-fg);
  padding: 5px 12px;
  border-radius: 7px;
  background: var(--wb-elevated);
  border: 1px solid var(--wb-border);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.editor-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.bar-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 13px;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  font-family: var(--font-body);
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.bar-btn.ghost {
  border: 1px solid var(--wb-border);
  background: transparent;
  color: var(--wb-fg);
}

.bar-btn.ghost:hover {
  background: var(--wb-hover);
}

.bar-btn.solid {
  border: none;
  background: var(--accent);
  color: #fff;
}

.bar-btn.solid:hover {
  background: var(--accent-hover);
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
  color: var(--wb-fg);
}

.code-empty-hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--wb-muted);
  max-width: 320px;
}
</style>
