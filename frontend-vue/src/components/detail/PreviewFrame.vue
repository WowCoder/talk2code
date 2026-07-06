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
      <div class="toolbar-spacer"></div>
      <button class="device-btn" @click="refreshPreview" title="刷新预览">
        ↻ 刷新
      </button>
      <!-- 预览验证状态指示灯 -->
      <span
        v-if="previewStatus !== null"
        :class="['status-dot', previewStatus]"
        :title="previewTooltip"
      ></span>
    </div>
    <div class="preview-canvas">
      <div v-if="!hasContent" class="preview-empty">
        等待代码生成…
      </div>
      <div v-else class="preview-wrapper">
        <iframe
          :key="previewKey"
          :src="iframeSrc"
          :style="{ width: iframeWidth + 'px', maxWidth: '100%' }"
          class="preview-iframe"
          sandbox="allow-scripts allow-same-origin allow-forms"
        ></iframe>
      </div>
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
const previewKey = ref(0)

const iframeWidth = computed(() =>
  devices.find((d) => d.key === activeDevice.value)?.width || 1024
)

const hasContent = computed(() => {
  // 不仅 key 存在，内容也必须非空（生成中可能是空字符串）
  const html = store.codeFiles['index.html']
  return !!html && html.trim().length > 0
})

// 预览 URL 指向后端文件服务端点，相对路径自动解析
const iframeSrc = computed(() => {
  const reqId = store.currentRequirement?.id
  if (!reqId) return ''
  return `/api/preview/${reqId}/index.html`
})

// 预览验证状态
type PreviewStatusType = 'passed' | 'failed' | 'unavailable' | null
const previewStatus = ref<PreviewStatusType>(null)
const previewTooltip = ref('')
const previewErrors = ref<string[]>([])

function refreshPreview() {
  previewKey.value++
}

function setDevice(key: string) {
  activeDevice.value = key
  localStorage.setItem('previewDevice', key)
}

// 监听 codeFiles 变化，自动刷新预览
watch(
  () => store.codeFiles['index.html'],
  () => {
    refreshPreview()
  }
)

// 暴露方法供 SSE composable 调用，更新验证状态
;(window as any).__previewUpdateStatus = function (
  status: PreviewStatusType,
  errors: string[],
  tooltip: string
) {
  previewStatus.value = status
  previewErrors.value = errors
  previewTooltip.value = tooltip || (errors.length > 0 ? errors.join('; ') : '')
}
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

.toolbar-spacer {
  flex: 1;
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

/* 预览验证状态指示灯 */
.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-left: 4px;
}

.status-dot.passed {
  background: #4caf50;
  box-shadow: 0 0 4px rgba(76, 175, 80, 0.5);
}

.status-dot.failed {
  background: #f44336;
  box-shadow: 0 0 4px rgba(244, 67, 54, 0.5);
}

.status-dot.unavailable {
  background: #9e9e9e;
}

.preview-canvas {
  flex: 1;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
}

.preview-wrapper {
  height: 100%;
  width: 100%;
  display: flex;
  justify-content: center;
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
