<template>
  <div class="preview-panel">
    <div class="preview-toolbar">
      <button
        v-for="d in devices"
        :key="d.key"
        :class="['device-btn', { active: activeDevice === d.key }]"
        @click="setDevice(d.key)"
      >
        {{ d.label }}
      </button>
    </div>
    <div class="preview-canvas">
      <div v-if="!hasContent" class="preview-empty">
        等待代码生成…
      </div>
      <iframe
        v-else
        :src="iframeSrc"
        :style="{ width: iframeWidth + 'px', maxWidth: '100%' }"
        class="preview-iframe"
        sandbox="allow-scripts allow-same-origin allow-forms"
      ></iframe>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRequirementStore } from '@/stores/requirement'

const store = useRequirementStore()

interface Device {
  key: string
  label: string
  width: number
}

const devices: Device[] = [
  { key: 'desktop', label: '桌面端', width: 1024 },
  { key: 'tablet', label: '平板', width: 768 },
  { key: 'mobile', label: '手机', width: 375 },
]

const activeDevice = ref(localStorage.getItem('previewDevice') || 'desktop')

const iframeWidth = computed(() =>
  devices.find((d) => d.key === activeDevice.value)?.width || 1024
)

const hasContent = computed(() => {
  return Object.keys(store.codeFiles).length > 0
})

function buildPreviewHTML(): string {
  const html = store.codeFiles['index.html'] || ''
  const css = store.codeFiles['style.css'] || ''
  const js = store.codeFiles['script.js'] || ''

  if (!html && !css && !js) return ''

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com;script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com;style-src 'self' 'unsafe-inline';img-src 'self' data: https:">
  <title>Preview</title>
  <style>${css}</style>
</head>
<body>${html}
<script>${js}<\/script>
</body></html>`
}

const iframeSrc = ref('')

function refreshPreview() {
  const html = buildPreviewHTML()
  if (!html) {
    iframeSrc.value = ''
    return
  }
  const blob = new Blob([html], { type: 'text/html' })
  // Revoke old URL to prevent memory leaks
  if (iframeSrc.value) {
    URL.revokeObjectURL(iframeSrc.value)
  }
  iframeSrc.value = URL.createObjectURL(blob)
}

function setDevice(key: string) {
  activeDevice.value = key
  localStorage.setItem('previewDevice', key)
}

// Watch codeFiles for changes
watch(
  () => ({ ...store.codeFiles }),
  () => {
    refreshPreview()
  },
  { deep: true }
)
</script>

<style scoped>
.preview-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.preview-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: var(--dark-surface);
  border-bottom: 1px solid var(--dark-border);
  flex-shrink: 0;
}

.device-btn {
  padding: 5px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-family: var(--font-body);
  color: var(--dark-muted);
  cursor: pointer;
  border: 1px solid var(--dark-border);
  background: transparent;
  transition: color 0.15s, border-color 0.15s;
}

.device-btn:hover {
  color: var(--dark-fg);
  border-color: oklch(45% 0.012 60);
}

.device-btn.active {
  color: #fff;
  background: oklch(30% 0.012 60);
  border-color: oklch(45% 0.012 60);
}

.preview-canvas {
  flex: 1;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
}

.preview-empty {
  color: #999;
  text-align: center;
  padding: 40px;
  font-size: 14px;
}

.preview-iframe {
  height: 100%;
  border: none;
  background: #fff;
}
</style>
