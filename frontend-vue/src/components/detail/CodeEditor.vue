<template>
  <div ref="editorContainer" class="code-editor-container"></div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState, Compartment, type Extension } from '@codemirror/state'
import { html } from '@codemirror/lang-html'
import { css } from '@codemirror/lang-css'
import { javascript } from '@codemirror/lang-javascript'
import { keymap } from '@codemirror/view'
import { useSettingsStore } from '@/stores/settings'

const props = defineProps<{
  filename: string
  content: string
  darkMode?: boolean
  fontSize?: string
}>()

const emit = defineEmits<{
  'update:content': [content: string]
}>()

const settingsStore = useSettingsStore()
const editorContainer = ref<HTMLElement>()
let view: EditorView | null = null
let saveTimer: ReturnType<typeof setTimeout> | null = null
// 程序化 setValue 时置位，跳过由此触发的 debounced emit（避免外部更新造成冗余回存）
let suppressNextEmit = false

function getLanguageExtension(filename: string): Extension {
  const ext = filename.split('.').pop()?.toLowerCase()
  switch (ext) {
    case 'css': return css()
    case 'js': case 'ts': case 'jsx': case 'tsx': return javascript()
    case 'html': default: return html()
  }
}

function createTheme(isDark: boolean): Extension {
  const fontSize = props.fontSize || '13px'

  if (isDark) {
    return EditorView.theme({
      '&':             { height: '100%', fontSize },
      '.cm-scroller':  { fontFamily: "'JetBrains Mono', ui-monospace, monospace", lineHeight: '1.7' },
      '.cm-content':   { caretColor: '#fff' },
      '.cm-gutters':   { backgroundColor: 'oklch(18% 0.012 60)', color: 'oklch(58% 0.01 60)', border: 'none' },
      '.cm-activeLineGutter': { backgroundColor: 'oklch(28% 0.012 60)' },
      '.cm-activeLine': { backgroundColor: 'oklch(28% 0.015 60 / 50%)' },
      '.cm-cursor':    { borderLeftColor: '#fff' },
      '.cm-selectionBackground': { backgroundColor: 'oklch(40% 0.08 250 / 30%)' },
    }, { dark: true })
  }

  return EditorView.theme({
    '&':             { height: '100%', fontSize },
    '.cm-scroller':  { fontFamily: "'JetBrains Mono', ui-monospace, monospace", lineHeight: '1.7' },
    '.cm-content':   { caretColor: '#000' },
    '.cm-gutters':   { backgroundColor: 'oklch(96% 0.005 60)', color: 'oklch(55% 0.018 50)', border: 'none' },
    '.cm-activeLineGutter': { backgroundColor: 'oklch(90% 0.01 60)' },
    '.cm-activeLine': { backgroundColor: 'oklch(88% 0.015 60 / 50%)' },
    '.cm-cursor':    { borderLeftColor: '#000' },
    '.cm-selectionBackground': { backgroundColor: 'oklch(80% 0.08 250 / 30%)' },
  }, { dark: false })
}

const themeCompartment = new Compartment()
const languageCompartment = new Compartment()

function buildExtensions(): Extension[] {
  return [
    basicSetup,
    languageCompartment.of(getLanguageExtension(props.filename)),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        // 程序化更新（props.content 变化回填）不触发保存
        if (suppressNextEmit) {
          suppressNextEmit = false
          if (saveTimer) {
            clearTimeout(saveTimer)
            saveTimer = null
          }
          return
        }
        if (saveTimer) clearTimeout(saveTimer)
        saveTimer = setTimeout(() => {
          emit('update:content', update.state.doc.toString())
        }, 1000)
      }
    }),
    keymap.of([]),
    themeCompartment.of(createTheme(settingsStore.darkMode)),
    EditorState.tabSize.of(2),
  ]
}

function createEditor() {
  if (!editorContainer.value) return
  const state = EditorState.create({
    doc: props.content || '',
    extensions: buildExtensions(),
  })
  view = new EditorView({ state, parent: editorContainer.value })
}

function updateContent(newContent: string) {
  if (!view) return
  const current = view.state.doc.toString()
  if (newContent !== current) {
    suppressNextEmit = true
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: newContent },
    })
  }
}

// Theme change: reconfigure compartment without destroying editor
watch(
  () => settingsStore.darkMode,
  (isDark) => {
    if (view) {
      view.dispatch({
        effects: themeCompartment.reconfigure(createTheme(isDark)),
      })
    }
  }
)

// Filename change: reconfigure language compartment
watch(
  () => props.filename,
  (filename) => {
    if (view) {
      view.dispatch({
        effects: languageCompartment.reconfigure(getLanguageExtension(filename)),
      })
    }
  }
)

watch(() => props.content, (newVal) => updateContent(newVal))

onMounted(() => createEditor())
onUnmounted(() => {
  if (saveTimer) clearTimeout(saveTimer)
  if (view) {
    view.destroy()
    view = null
  }
})
</script>

<style scoped>
.code-editor-container {
  height: 100%;
  overflow: auto;
}
</style>
