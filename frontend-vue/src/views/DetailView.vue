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
      <DialoguePanel @send-message="onSendMessage" @stop="onStopGeneration" />

      <!-- Right: Preview / Code -->
      <div class="right-panel">
        <ProgressBar :percent="store.progress.percent" />
        <PanelTabs
          v-model:activeTab="activeTab"
          @download="onDownload"
        />

        <!-- Spec view -->
        <div v-show="activeTab === 'spec'" class="view active">
          <SpecPanel
            :spec-data="store._specData"
            :evaluator-result="store.evaluatorResult"
          />
        </div>

        <!-- Tasks view -->
        <div v-show="activeTab === 'tasks'" class="view active">
          <TaskPanel :tasks="store._taskList || []" />
        </div>

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
import SpecPanel from '@/components/detail/SpecPanel.vue'
import TaskPanel from '@/components/detail/TaskPanel.vue'
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
const { connect, disconnect, isConnected, connectionError } = useSSE(reqId)

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
  if (store.currentRequirement?.status === 'finished_with_issues') return '已完成 (有问题)'
  if (store.currentRequirement?.status === 'failed') return '失败'
  return '准备中'
})

const tokenInfo = computed(() => {
  const trace = store._traceSummary as SSETraceSummaryData | null
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
    // 竞态保护：本次加载已被更新的请求取代
    if (!data) return

    const req = store.currentRequirement
    if (!req) return

    if (req.status === 'finished' || req.status === 'finished_with_issues') {
      store.isGenerating = false
      store.progress = { currentAgent: '', percent: 100 }
      // trace / evaluator 已在 loadRequirement 内恢复
    } else if (req.status === 'processing' || req.status === 'planning') {
      // 进行中状态：连接 SSE 并锁定输入
      store.isGenerating = true
      connect()
    } else {
      // pending 状态：连接 SSE 但不立即显示"工作中"
      // isGenerating 由 progress 事件或 SSE 连接状态触发
      connect()
    }
  } catch (err: any) {
    show(err.message || '加载需求失败', 'error')
  }
})

// Auto-switch tabs on SSE events
watch(() => store.planStatus, (status) => {
  if (status === 'needs_confirmation') {
    activeTab.value = 'spec'  // TL 完成后自动切到 Spec Tab
  } else if (status === 'confirmed') {
    activeTab.value = 'tasks' // 用户确认后自动切到任务 Tab
  }
})

// SSE 连接状态 → 生成中状态：仅在需求处于进行中状态时连接成功才锁定输入，
// 避免把 pending / finished 等普通浏览态误锁死
watch(isConnected, (connected) => {
  const st = store.currentRequirement?.status
  const inProgress = st === 'processing' || st === 'planning'
  if (connected && inProgress) {
    store.isGenerating = true
  }
})

// 连接异常提示（退避重连耗尽或鉴权失效）
watch(connectionError, (msg) => {
  if (msg) show(msg, 'error')
})

// Handle SSE disconnection when leaving
watch(reqId, (newId, oldId) => {
  if (oldId) disconnect()
  if (newId) {
    store.reset()
    store.loadRequirement(newId)
      .then((data) => {
        // 竞态保护：本次加载已被更新的请求取代
        if (!data) return
        connect()
      })
      .catch((err: any) => {
        show(err.message || '加载需求失败', 'error')
      })
  }
})

// Chat send handler
async function onSendMessage(
  message: string,
  clarify?: { questions: any[]; answers: Record<string, string> }
) {
  // 消息以 [用户补充说明] 开头说明是澄清后的合成消息，
  // 已提交卡片由 DialoguePanel 落入消息流，不重复添加纯文本消息
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
    const result = await store.sendChatMessage(message, clarify)
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

// Stop handler: 取消正在执行的 Agent 任务
async function onStopGeneration() {
  if (!store.currentRequirement?.id) return

  try {
    await fetch(`/api/requirements/${store.currentRequirement.id}/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
    })
    // SSE cancelled 事件会自动清理 isGenerating 状态
    // 作为 fallback，也在这里清理
    store.isGenerating = false
    store.progress = { currentAgent: '', percent: 0 }
  } catch (err: any) {
    // 即使请求失败，也恢复输入状态
    store.isGenerating = false
    show('取消失败: ' + (err.message || '未知错误'), 'error')
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
${escapeInlineScript(files[key])}
</${'script'}>`
        }
      }
      return match // 未找到则保留原始标签（如 CDN 外部引用）
    }
  )

  return html
}

// 内联 JS 前把内容里的 script 闭合标签转义，避免破坏 HTML 结构
function escapeInlineScript(content: string): string {
  return content.replace(/<\/script/gi, '<\\/script')
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
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

/* Responsive: stack vertically on narrow screens */
@media (max-width: 768px) {
  .split {
    flex-direction: column;
  }
  .right-panel {
    min-height: 50vh;
  }
}
</style>
