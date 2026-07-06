<template>
  <div class="detail-page">
    <!-- Nav -->
    <AppNav
      compact
      :title="pageTitle"
      :status-text="statusText"
      :is-active="store.isGenerating"
    />

    <!-- Split layout -->
    <div class="split">
      <!-- Left: Dialogue -->
      <DialoguePanel @send-message="onSendMessage" />

      <!-- Right: Preview / Code -->
      <div class="right-panel">
        <ProgressBar :percent="store.progress.percent" />
        <PanelTabs
          v-model:activeTab="activeTab"
          @download="onDownload"
        />

        <!-- Preview view -->
        <div v-show="activeTab === 'preview'" class="view active">
          <PreviewFrame />
        </div>

        <!-- Code view -->
        <div v-show="activeTab === 'code'" class="view active">
          <CodePanel />
        </div>

        <TokenBar
          :tokens="tokenInfo.totalTokens"
          :cost="tokenInfo.totalCost"
          :time-ms="tokenInfo.totalDurationMs"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useRequirementStore } from '@/stores/requirement'
import { useToast } from '@/composables/useToast'
import { useSSE } from '@/composables/useSSE'
import AppNav from '@/components/layout/AppNav.vue'
import DialoguePanel from '@/components/detail/DialoguePanel.vue'
import ProgressBar from '@/components/detail/ProgressBar.vue'
import PanelTabs from '@/components/detail/PanelTabs.vue'
import PreviewFrame from '@/components/detail/PreviewFrame.vue'
import CodePanel from '@/components/detail/CodePanel.vue'
import TokenBar from '@/components/detail/TokenBar.vue'
import type { SSETraceSummaryData } from '@/types/sse'

const route = useRoute()
const router = useRouter()
const store = useRequirementStore()
const { show } = useToast()
const activeTab = ref('preview')

// SSE connection
const reqId = computed(() => {
  const id = route.params.id
  return id ? Number(id) : null
})
const { connect, disconnect } = useSSE(reqId)

const pageTitle = computed(() => {
  const req = store.currentRequirement
  return req?.title || req?.content || '加载中…'
})

const statusText = computed(() => {
  if (store.isGenerating) {
    const agent = store.progress.currentAgent || 'Agent'
    return `${agent} 工作中…`
  }
  if (store.currentRequirement?.status === 'finished') return '已完成'
  if (store.currentRequirement?.status === 'failed') return '失败'
  return '准备中'
})

const tokenInfo = computed(() => {
  const trace = (store as any)._traceSummary as SSETraceSummaryData | null
  return {
    totalTokens: trace?.total_tokens || 0,
    totalCost: trace?.total_cost || 0,
    totalDurationMs: trace?.total_duration_ms || 0,
  }
})

// Load requirement, then decide to connect SSE
onMounted(async () => {
  if (!reqId.value) {
    router.push('/')
    return
  }

  try {
    const data = await store.loadRequirement(reqId.value)

    const req = store.currentRequirement
    if (!req) return

    if (req.status === 'finished') {
      store.isGenerating = false
      store.progress = { currentAgent: '', percent: 100 }
      // 已完成的请求通过 API response 获取 trace 数据
      if ((data as any).trace) {
        ;(store as any)._traceSummary = (data as any).trace
      }
    } else if (req.status === 'processing') {
      // 正在处理中：连接 SSE 并显示处理状态
      store.isGenerating = true
      connect()
    } else {
      // pending 状态：连接 SSE 但不立即显示"工作中"
      // isGenerating 由 progress 事件或 isConnected 触发
      connect()
    }
  } catch (err: any) {
    show(err.message || '加载需求失败', 'error')
  }
})

// Handle SSE disconnection when leaving
watch(reqId, (newId, oldId) => {
  if (oldId) disconnect()
  if (newId) {
    store.reset()
    store.loadRequirement(newId).then((data) => {
      if ((data as any)?.trace) {
        ;(store as any)._traceSummary = (data as any).trace
      }
      connect()
    })
  }
})

// Chat send handler
async function onSendMessage(message: string) {
  // 消息以 [用户补充说明] 开头说明是澄清后的合成消息，
  // 不重复添加到对话（store.sendChatMessage 会用服务端响应替换）
  const isClarifyFollowUp = message.startsWith('[用户补充说明]')

  if (!isClarifyFollowUp) {
    store.addDialogueMessage({
      role: 'user',
      name: '用户',
      content: message,
    })
  }

  store.isGenerating = true

  // 确保 SSE 已连接，否则后端推送的实时事件无法被接收
  connect()

  try {
    const result = await store.sendChatMessage(message)
    if (result?.needs_clarification) {
      // 修改意见模糊，暂停执行等待用户补充信息
      store.pendingChatClarification = { originalMessage: message }
      store.isGenerating = false
      return
    }
  } catch (err: any) {
    show(err.message || '发送失败', 'error')
    // 失败时保留用户消息，不清除 isGenerating 状态
    store.addDialogueMessage({
      role: 'system',
      content: `发送失败：${err.message || '未知错误'}`,
    })
  } finally {
    if (!store.pendingChatClarification) {
      store.isGenerating = false
    }
  }
}

// Download handler: 将 index.html 及相关资源打包为独立 HTML
function onDownload() {
  const files = { ...store.codeFiles }
  const indexHtml = files['index.html'] || ''

  if (!indexHtml) {
    show('没有可下载的代码', 'error')
    return
  }

  // 如果 index.html 包含完整 DOCTYPE，直接内联所有 CSS/JS 引用
  const content = buildStandaloneHTML(files)
  const blob = new Blob([content], { type: 'text/html' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'app.html'
  a.click()
  URL.revokeObjectURL(url)
  show('下载完成', 'success')
}

function buildStandaloneHTML(files: Record<string, string>): string {
  let html = files['index.html'] || ''

  // 替换 <link rel="stylesheet" href="..."> 为内联 <style>
  html = html.replace(
    /<link\s+[^>]*rel=["']stylesheet["'][^>]*href=["']([^"']+)["'][^>]*>/gi,
    (match: string, href: string) => {
      // 查找相对于 index.html 的 CSS 文件
      const candidates = [href, href.replace(/^\.\//, '')]
      for (const key of candidates) {
        if (files[key]) {
          return `<style>/* ${key} */\n${files[key]}\n</style>`
        }
      }
      return match // 未找到则保留原始标签
    }
  )

  // 替换 <script src="..."> 为内联 <script>
  html = html.replace(
    /<script\s+[^>]*src=["']([^"']+)["'][^>]*>/gi,
    (match: string, src: string) => {
      const candidates = [src, src.replace(/^\.\//, '')]
      for (const key of candidates) {
        if (files[key]) {
          return `<script>/* ${key} */
${files[key]}
</${'script'}>`
        }
      }
      return match // 未找到则保留原始标签（如 CDN 外部引用）
    }
  )

  return html
}
</script>

<style scoped>
.detail-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--bg);
}

.split {
  flex: 1;
  display: flex;
  min-height: 0;
  gap: 1px;
  background: var(--border);
}

.right-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--dark-bg);
  min-width: 0;
}

.view {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
</style>
