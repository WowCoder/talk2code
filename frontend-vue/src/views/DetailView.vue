<template>
  <div class="detail-page">
    <!-- Nav -->
    <AppNav
      compact
      :title="pageTitle"
      :status-text="statusText"
      :is-active="store.isGenerating"
    />

    <!-- 中断提示：服务重启会中断在跑的需求，进度已保留在检查点，可一键续跑 -->
    <div v-if="store.currentRequirement?.status === 'interrupted'" class="resume-banner">
      <span class="resume-text">上次执行被服务重启中断，进度已保留，可从断点继续。</span>
      <button class="resume-btn" :disabled="resuming" @click="onResume">
        {{ resuming ? '正在继续…' : '继续' }}
      </button>
    </div>

    <!-- 生成失败：失败后代码 TAB 与预览 TAB 都是全空的，而失败信号只有导航栏一枚
         小徽章，用户既不知道发生了什么，也不知道还能再来一次。这里给出可读原因
         与重试入口——后端 /resume 的放行闸门包含 failed，可安全重新入队。 -->
    <div v-else-if="store.currentRequirement?.status === 'failed'" class="resume-banner failed">
      <span class="resume-text">{{ failureText }}</span>
      <button class="resume-btn" :disabled="resuming" @click="onResume">
        {{ resuming ? '正在重新生成…' : '重新生成' }}
      </button>
    </div>

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
        <div v-show="activeTab === 'preview'" class="view active preview-wrap">
          <!-- QA 验收中：Catherine 正在浏览器里逐步操作，覆盖提示让用户知道画面在被驱动 -->
          <div v-if="store.qaRunning" class="qa-running-banner">
            <span class="qa-running-dot"></span>
            <span>🔍 QA 正在验收…</span>
            <span v-if="currentAcLabel" class="qa-running-ac">{{ currentAcLabel }}</span>
            <span v-if="currentAcSteps" class="qa-running-steps">第 {{ currentAcSteps }} 步</span>
          </div>
          <PreviewFrame />
        </div>

        <!-- Code view -->
        <div v-show="activeTab === 'code'" class="view active">
          <CodePanel />
        </div>

        <!-- Publish view -->
        <div v-show="activeTab === 'publish'" class="view active">
          <PublishPanel @resume="onResume" />
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
import { useRequirementStore, emptyProgress } from '@/stores/requirement'
import { useAuthStore } from '@/stores/auth'
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
import PublishPanel from '@/components/detail/PublishPanel.vue'
import TokenBar from '@/components/detail/TokenBar.vue'
import type { SSETraceSummaryData } from '@/types/sse'

const route = useRoute()
const router = useRouter()
const store = useRequirementStore()
const authStore = useAuthStore()
const { show } = useToast()
const activeTab = ref('preview')

// SSE connection
const reqId = computed(() => {
  const id = route.params.id
  return id ? Number(id) : null
})
const { connect, disconnect, isConnected, connectionError, serverElapsedS } = useSSE(reqId)

const pageTitle = computed(() => {
  const req = store.currentRequirement
  return req?.title || req?.content || '加载中…'
})

const statusText = computed(() => {
  if (store.isGenerating) {
    // 后端已改为推动作描述（"正在创建 js/app.js"），不再是角色名，直接展示。
    const action = store.progress.currentAgent || '正在处理'
    // 心跳累计超过 15s 说明正处于静默期（LLM 请求可能挂起 60~150s）。把等待时长显式
    // 说出来，否则用户面对的只是一个不动的界面，只能猜「是不是卡住了」。
    if (serverElapsedS.value >= 15) {
      const m = Math.floor(serverElapsedS.value / 60)
      const sec = serverElapsedS.value % 60
      const waited = m > 0 ? `${m} 分 ${sec} 秒` : `${sec} 秒`
      return `${action} · 已等待 ${waited}`
    }
    return action
  }
  if (store.currentRequirement?.status === 'finished') return '已完成'
  if (store.currentRequirement?.status === 'finished_with_issues') return '已完成 (有问题)'
  if (store.currentRequirement?.status === 'needs_user_input') return '待用户处理（存在关键缺陷）'
  if (store.currentRequirement?.status === 'failed') return '失败'
  if (store.currentRequirement?.status === 'planning') return '等待确认开发计划'
  return '准备中'
})

// 失败提示文案：后端 error_message 是技术原文（例如
// "HTTPSConnectionPool(host='api.lkeap...'): Read timed out. (read timeout=300)"），
// 直接铺给用户等于没说。这里识别几类已知故障给出人话，其余降级为通用表述。
const failureText = computed(() => {
  const req = store.currentRequirement
  if (!req) return ''
  const raw = req.error_message || ''
  // 产物判定与发布门禁同源（API 快照 ∪ SSE 实时增量），否则失败文案会与实际
  // 「有没有代码可看」打架：快照为空但实时已产出时，文案会谎称没产出任何代码。
  const produced = store.hasProducedFiles
  const outcome = produced
    ? '这次生成没有跑完，已产出的代码仍然可用'
    : '这次生成没能产出任何代码'
  if (/timed ?out/i.test(raw)) return `${outcome}：模型服务响应超时。`
  if (/取消|cancel/i.test(raw)) return `${outcome}：任务已取消。`
  return `${outcome}。可以重新生成一次，或在对话里补充要求后再试。`
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

    if (req.status === 'finished' || req.status === 'finished_with_issues' || req.status === 'needs_user_input') {
      store.isGenerating = false
      store.progress = { ...emptyProgress(), percent: 100 }
      // trace / evaluator 已在 loadRequirement 内恢复
    } else if (req.status === 'processing') {
      // 真正有任务在跑：连接 SSE 并锁定输入
      store.isGenerating = true
      connect()
    } else {
      // pending / planning：planning 表示「TL 已出计划，等你点确认」，此刻队列里
      // 没有任务在跑——按"生成中"渲染就会一边让你确认计划、一边转着工作指示灯
      //（req 164 的观感就是"一直卡在编码"）。isGenerating 由 progress 事件触发。
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

// QA 验收阶段自动切到预览 Tab：Catherine 正在浏览器里操作，
// 用户应该看到的是被操作的页面，而不是停在对话流里干等
watch(() => store.qaRunning, (running) => {
  if (running) activeTab.value = 'preview'
})

// 当前正在验收的 AC（用于预览区顶部提示）
const currentAc = computed(() => {
  const list = store.dialogueMessages
  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i].role === 'qa_result') return list[i] as any
  }
  return null
})
const currentAcLabel = computed(() => {
  const ac = currentAc.value
  if (!ac) return ''
  const r = ac.qa_result || {}
  return `[${r.ac_id || ''}] ${r.label || ''}`.trim()
})
const currentAcSteps = computed(() => {
  const ac = currentAc.value
  if (!ac?.qa_result?.steps) return 0
  return ac.qa_result.steps.length
})

// SSE 连接状态 → 生成中状态：仅在需求处于进行中状态时连接成功才锁定输入，
// 避免把 pending / finished 等普通浏览态误锁死
watch(isConnected, (connected) => {
  const st = store.currentRequirement?.status
  // planning 表示「TL 已出计划，等你点确认」，此刻队列里没有任务在跑。
  // 把它算成进行中会让界面一直转着「工作中」，而实际上正停在一个需要你操作
  // 的选择点上（req 164：确认卡片就在眼前，顶部却还在说"正在处理"）。
  const inProgress = st === 'processing'
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
  // 演示模式只读：后端有 demo 守卫兜底，这里提前拦截给更好的引导
  if (authStore.isDemo) {
    show('演示模式为只读，注册后即可继续对话修改', 'info')
    return
  }

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
  if (authStore.isDemo) {
    show('演示模式为只读，不可取消任务', 'info')
    return
  }

  try {
    await fetch(`/api/requirements/${store.currentRequirement.id}/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
    })
    // SSE cancelled 事件会自动清理 isGenerating 状态
    // 作为 fallback，也在这里清理
    store.isGenerating = false
    store.progress = emptyProgress()
  } catch (err: any) {
    // 即使请求失败，也恢复输入状态
    store.isGenerating = false
    show('取消失败: ' + (err.message || '未知错误'), 'error')
  }
}

// Resume handler: 从检查点继续被中断的需求
const resuming = ref(false)
async function onResume() {
  if (resuming.value || !store.currentRequirement?.id) return
  if (authStore.isDemo) {
    show('演示模式为只读，注册后即可续跑任务', 'info')
    return
  }
  resuming.value = true
  const wasFailed = store.currentRequirement.status === 'failed'
  try {
    await store.resumeRequirement()
    // 后端已置为 processing；同步本地状态让提示条立即收起（SSE 随后会推送真实进度）
    store.currentRequirement.status = 'processing'
    // 必须重新挂上 SSE：失败/中断的需求在页面挂载时连过一次，但那时后端没有任务在跑，
    // 推送一直空转。不重连的话点了「重新生成」界面毫无反应，用户只能自己刷新页面猜。
    connect()
    show(wasFailed ? '已重新入队，正在重新生成' : '已从断点继续', 'success')
  } catch (err: any) {
    resuming.value = false
    show('继续失败: ' + (err.message || '未知错误'), 'error')
    return
  }
  resuming.value = false
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

/* 中断提示条：横贯在导航与分栏之间，不挤占左右面板 */
.resume-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  background: var(--accent-soft);
  border-bottom: 1px solid var(--border);
  color: var(--fg);
  font-size: 13px;
}

.resume-text {
  min-width: 0;
}

/* 失败态：与中断态（accent-soft）区分开——中断是"可以接着来"，失败是"这次没成" */
.resume-banner.failed {
  background: #fffbeb;
  border-bottom-color: #fde68a;
  color: #92400e;
}
.resume-banner.failed .resume-btn {
  background: #d97706;
}

.resume-btn {
  flex-shrink: 0;
  padding: 6px 16px;
  border: none;
  border-radius: 6px;
  background: var(--accent);
  color: var(--color-on-accent);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.resume-btn:hover:not(:disabled) {
  filter: brightness(1.06);
}

.resume-btn:disabled {
  opacity: 0.6;
  cursor: default;
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

/* QA 验收中提示条：Catherine 正在驱动浏览器，预览画面在被真实操作 */
.preview-wrap {
  position: relative;
}

.qa-running-banner {
  position: absolute;
  top: 8px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 12px;
  border-radius: 999px;
  background: rgba(59, 130, 246, .94);
  color: #fff;
  font-size: 12px;
  font-weight: 500;
  box-shadow: 0 2px 10px rgba(0, 0, 0, .18);
  pointer-events: none;
  white-space: nowrap;
  max-width: 92%;
}
.qa-running-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #fff;
  animation: qa-running-pulse 1.2s ease-in-out infinite;
}
@keyframes qa-running-pulse { 0%, 100% { opacity: 1 } 50% { opacity: .25 } }
.qa-running-ac {
  opacity: .92;
  overflow: hidden;
  text-overflow: ellipsis;
}
.qa-running-steps {
  opacity: .8;
  font-variant-numeric: tabular-nums;
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
