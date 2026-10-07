import { ref, type Ref, onUnmounted } from 'vue'
import { useRequirementStore, emptyProgress } from '@/stores/requirement'
import { usePreviewStore } from '@/stores/preview'
import { useAuthStore } from '@/stores/auth'
import router from '@/router'
import type { RequirementStatus, DialogueMessage } from '@/types/api'
import type {
  SSEDialogueData,
  SSECodeData,
  SSEProgressData,
  SSEHeartbeatData,
  SSEQuestionFormData,
  SSEThinkingData,
  SSEHookCheckData,
  SSECompleteData,
  SSETraceSummaryData,
  SSEErrorData,
  SSEPreviewData,
  SSESpecData,
  SSETaskListData,
  SSETaskUpdateData,
  SSEChecklistUpdateData,
  SSEEvaluatorResultData,
  SSEIterationBatchData,
  SSEIterationStartData,
  SSEIterationAppendData,
  SSEIterationEndData,
  SSEQAStepData,
  SSEQAAcData,
  SSEVerifyStartData,
  SSEVerifyStepData,
} from '@/types/sse'

const INITIAL_RETRY_DELAY = 1000
const MAX_RETRY_DELAY = 30000
const MAX_RETRIES = 10

/** complete 事件允许下发的终态（与后端 requirement.status 的终态取值一致） */
const TERMINAL_STATUSES: RequirementStatus[] = [
  'finished',
  'finished_with_issues',
  'needs_user_input',
  'failed',
]

export function useSSE(reqId: Ref<number | null>) {
  const store = useRequirementStore()
  const previewStore = usePreviewStore()
  const authStore = useAuthStore()
  const eventSource = ref<EventSource | null>(null)
  const isConnected = ref(false)
  const connectionError = ref('')
  // 服务端心跳：静默期（LLM 挂起）的唯一活性信号，供 UI 显示「仍在处理 · 已等待 Ns」
  const serverElapsedS = ref(0)
  const lastHeartbeatAt = ref(0)

  // 指数退避重连状态（EventSource 会自动重连，这里关闭后由自己按退避策略调度）
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let retryCount = 0
  let retryDelay = INITIAL_RETRY_DELAY
  let stopped = false
  // 连接代数：探测鉴权的异步窗口内若有新连接建立，则放弃本次重连调度
  let connectEpoch = 0
  // 首连标记：首次 onopen 时对话已通过 loadRequirement 完整加载，无需再补齐；
  // 仅重连（非首次）成功时才调用 fetchDialogue 增量补齐断连窗口漏掉的历史消息。
  let firstConnect = true

  function connect() {
    if (!reqId.value) return
    connectEpoch++
    const epoch = connectEpoch
    disconnect()
    stopped = false
    connectionError.value = ''

    const es = new EventSource(`/api/sse/${reqId.value}`)
    eventSource.value = es

    // 连接成功后重置退避计数
    es.onopen = () => {
      isConnected.value = true
      retryCount = 0
      retryDelay = INITIAL_RETRY_DELAY
      // 重连成功后增量补齐断连窗口漏掉的历史消息（迭代轮次、QA 验收步骤等）。
      // 首连跳过：loadRequirement 已加载完整对话，重复补齐无意义且会造成一闪。
      if (!firstConnect) {
        store.fetchDialogue().catch(() => {})
      }
      firstConnect = false
    }

    es.addEventListener('connected', () => {
      isConnected.value = true
    })

    // 心跳：把「服务端还在干活」变成前端可见的状态（此前静默期前端一无所知）
    es.addEventListener('heartbeat', (e: MessageEvent) => {
      try {
        const data: SSEHeartbeatData = JSON.parse(e.data)
        serverElapsedS.value = data.elapsed_s || 0
        store.serverElapsedS = data.elapsed_s || 0
      } catch {
        serverElapsedS.value = 0
      }
      lastHeartbeatAt.value = Date.now()
    })

    es.addEventListener('dialogue', (e: MessageEvent) => {
      const data: SSEDialogueData = JSON.parse(e.data)
      store.addDialogueMessage({
        role: (data.role as 'user' | 'agent' | 'system') || 'agent',
        name: data.name,
        content: data.content,
        timestamp: data.timestamp,
      })
    })

    es.addEventListener('code', (e: MessageEvent) => {
      const data: SSECodeData = JSON.parse(e.data)
      store.updateCodeFiles(data)
    })

    es.addEventListener('progress', (e: MessageEvent) => {
      const data: SSEProgressData = JSON.parse(e.data)
      // SSE 重连会整段回放缓冲的历史 progress。需求停在等确认（planning）或
      // 已终止时，这些旧事件不能把界面拉回"生成中"——req 164 就是这么出现
      // 「一边让你确认计划、一边亮着编码阶段灯」的。
      const st = store.currentRequirement?.status
      if (st !== 'pending' && st !== 'processing') return
      // 桌面上摆着一张没提交的表单 = 流程停在等用户回答，此刻没有任务在跑。
      // SSE 重连会整段回放 progress，把界面拉回"生成中"，于是出现
      // 「一边等你选视觉风格、一边显示需求分析已等待 9 分钟」（req 221 实测，
      // question-form 事件被上面的去重条件挡掉，清不回来，只能在这里拦）。
      if (store.questionForm) return
      store.isGenerating = true
      // 进度只增不减：后端在「修复轮次 / coder 重入」时会按**轮内**位置重新
      // 上报更小的值（这是有意的，见 backend/harness/observability/progress_plan.py），
      // 这里对同一需求取历史最大值，保证用户看到的进度条不会往回跳。
      const prevPercent = Number(store.progress.percent) || 0
      const nextPercent = Math.max(prevPercent, Number(data.progress) || 0)
      store.progress = {
        currentAgent: data.current_agent,
        percent: nextPercent,
        // 阶段缺省时沿用上一阶段：后端部分埋点不带 stage，避免指示器闪回未知
        stage: data.stage || store.progress.stage,
        updatedAt: Date.now(),
      }
    })

    es.addEventListener('question-form', (e: MessageEvent) => {
      const data: SSEQuestionFormData = JSON.parse(e.data)
      // 已提交的表单不再弹出浮动编辑框（消息流中已有已提交卡片）
      if (data.submitted) return
      // SSE 消息缓冲区回放防御：如果对话中已有已提交表单，忽略回放的旧事件
      if (store.dialogueMessages.some((m: any) => m.question_form?.submitted === true)) return
      // 避免重复设置（loadRequirement 已恢复时跳过）
      if (store.questionForm) return
      // 出现待填写的表单 = 流程停在等用户回答，此刻没有任务在跑。
      // 不清进度态会一边弹表单、一边显示「需求分析 · 已等待 9 分钟」，
      // 用户在等模型，模型在等他（req 221 实测，刷新页面才恢复正常）。
      // 与 spec 事件的处理同一口径：停在选择点时绝不显示"生成中"。
      store.isGenerating = false
      store.progress = emptyProgress()
      store.serverElapsedS = 0
      store.addDialogueMessage({
        role: 'system',
        name: 'System',
        content: '__QUESTION_FORM__',
        question_form: data,
      })
      store.questionForm = data
    })

    // tool_call / tool_result 已合并到 iteration_batch 中，不再作为独立消息展示
    // thinking 已合并到 iteration_batch 中，标记 hidden 让前端跳过渲染
    es.addEventListener('thinking', (e: MessageEvent) => {
      const data: SSEThinkingData = JSON.parse(e.data)
      // thinking 内容已合并到 iteration_batch 中，不再作为独立消息展示
      // （保留 handler 以兼容旧版后端，标记 hidden 让前端跳过渲染）
      store.addDialogueMessage({
        role: 'thinking',
        name: data.name || 'Thinking',
        content: data.content,
        hidden: true,
      })
    })

    es.addEventListener('tool_call', (_e: MessageEvent) => {
      // tool_call 已合并到 iteration_batch 中，不再作为独立消息展示
      // （保留 handler 以兼容旧版后端，静默丢弃）
    })

    es.addEventListener('tool_result', (_e: MessageEvent) => {
      // tool_result 已合并到 iteration_batch 中，不再作为独立消息展示
      // （保留 handler 以兼容旧版后端，静默丢弃）
    })

    es.addEventListener('iteration_batch', (e: MessageEvent) => {
      const data: SSEIterationBatchData = JSON.parse(e.data)
      const batchTools = data.tools || []
      // 跳过畸形事件：**只要没有操作列表，这张迭代卡片就没有存在意义**。
      //
      // 历史教训：早期只挡「轮次 / 工具 / 文本三者全空」的事件，结果仍会成批渲染出
      // 「Henry（开发工程师） 0 个操作」的幽灵卡片——那些事件带着 content 或 iteration，
      // 三者不全空，于是漏网；SSE 断线重连回放整段缓冲时更是一次性冒出七八张。
      // 后端 runtime.py 发 iteration_batch 前已用 `if batch_tools` 兜底，
      // 数据库里也不会存空 tools 的迭代记录，所以这里丢弃是安全的，不会误杀正常轮次。
      if (batchTools.length === 0) {
        // 不静默吞掉：留痕便于定位上游到底是谁在发无 tools 的事件
        console.warn('[SSE] 丢弃无操作列表的 iteration_batch 事件', {
          iteration: data.iteration,
          coder_name: data.coder_name,
          content: (data as any).content,
        })
        return
      }
      store.addDialogueMessage({
        role: 'iteration_batch',
        name: data.coder_name || 'Agent',
        content: (data as any).content || `第 ${data.iteration ?? '?'} 轮迭代 — ${batchTools.length} 个操作`,
        iteration: data.iteration,
        thinking_preview: data.thinking_preview,
        agent_text: data.agent_text,
        tools: batchTools,
      })
    })

    // ---- 迭代轮次实时累积（取代整轮一次性 iteration_batch）----
    // 一轮开始 → 前端创建可累积轮次卡片；过程每步 iteration_append 实时填充；
    // 轮次结束 → iteration_end 固定卡片。刷新页面时由 dialogue_history 的迭代记录恢复静态卡片。
    es.addEventListener('iteration_start', (e: MessageEvent) => {
      try {
        const data: SSEIterationStartData = JSON.parse(e.data)
        // 防御：没有 iteration 的畸形事件无法定位轮次，丢弃
        if (data.iteration == null) return
        store.startIteration(data)
      } catch {
        // ignore parse errors
      }
    })

    es.addEventListener('iteration_append', (e: MessageEvent) => {
      try {
        const data: SSEIterationAppendData = JSON.parse(e.data)
        store.appendIterationTool(data)
      } catch {
        // ignore parse errors
      }
    })

    es.addEventListener('iteration_end', (e: MessageEvent) => {
      try {
        const data: SSEIterationEndData = JSON.parse(e.data)
        if (data.iteration == null) return
        store.endIteration(data.iteration, (data as any).content)
      } catch {
        // ignore parse errors
      }
    })

    // QA 验收逐步操作：Catherine 在浏览器里的每一步（点击/输入/断言），
    // 追加进所属 AC 验收卡（一个验收项一张卡，不再逐条平铺）
    es.addEventListener('qa_step', (e: MessageEvent) => {
      try {
        const data: SSEQAStepData = JSON.parse(e.data)
        store.appendQaStep(data)
      } catch {
        // ignore parse errors
      }
    })

    // 一个验收项开始：建一张可实时累积的 AC 卡
    es.addEventListener('qa_start', (e: MessageEvent) => {
      try {
        const data: SSEQAAcData = JSON.parse(e.data)
        store.startQaAc(data)
      } catch {
        // ignore parse errors
      }
    })

    // 一个验收项结束：固定 AC 卡并给出结论
    es.addEventListener('qa_result', (e: MessageEvent) => {
      try {
        const data: SSEQAAcData = JSON.parse(e.data)
        store.endQaAc(data)
      } catch {
        // ignore parse errors
      }
    })

    // 验证阶段建卡：此刻已完成的子步骤（打开页面 / AC 验收 / 冒烟）随卡一起给，
    // 之后的契约检查 / DoD / 视觉证据 / 深度评估逐步实时更新。
    // 此前这四步全程零推送：req 207 实测界面停在「正在做通用交互冒烟测试」约 2 分钟。
    es.addEventListener('verify_start', (e: MessageEvent) => {
      try {
        const data: SSEVerifyStartData = JSON.parse(e.data)
        store.startVerifyTrace(data)
      } catch {
        // ignore parse errors
      }
    })

    es.addEventListener('verify_step', (e: MessageEvent) => {
      try {
        const data: SSEVerifyStepData = JSON.parse(e.data)
        store.updateVerifyStep(data)
      } catch {
        // ignore parse errors
      }
    })

    // 验证摘要卡（落库消息的实时副本）：走通用入列路径，因此与刷新后从
    // dialogue_history 恢复的同一条消息共用幂等键；入列时撤掉临时 live 卡。
    es.addEventListener('qa_summary', (e: MessageEvent) => {
      try {
        const data: SSEDialogueData = JSON.parse(e.data)
        store.addDialogueMessage(data as unknown as DialogueMessage)
      } catch {
        // ignore parse errors
      }
    })

    es.addEventListener('hook_check', (e: MessageEvent) => {
      const data: SSEHookCheckData = JSON.parse(e.data)
      // 失败项以 dialogue 消息形式展示（DialogueMessage 的 hook_check 分支渲染），
      // 否则用户对质量校验失败完全无感知
      if (!data.passed) {
        store.addDialogueMessage({
          role: 'hook_check',
          name: data.hook_name,
          hook_name: data.hook_name,
          passed: false,
          content: data.message || '',
        } as any)
      }
    })

    es.addEventListener('complete', (e: MessageEvent) => {
      const data: SSECompleteData = JSON.parse(e.data)
      store.isGenerating = false
      store.progress = { ...emptyProgress(), percent: 100 }
      // 同步后端权威终态。不同步的话「发布」TAB 会一直不可点：它的门禁要求
      // status === 'finished'，而 currentRequirement 是进页面时的 API 快照，
      // 永远不会自己变成 finished —— 用户只能手动刷新页面才能发布（req 202）。
      // 只接受终态白名单：中途态（processing 等）不该由 complete 事件下发，
      // 收到就说明协议出问题，宁可不同步也不要让状态机乱跳。
      if (data.status && store.currentRequirement
          && TERMINAL_STATUSES.includes(data.status)) {
        store.currentRequirement.status = data.status
      }
      if (data.code_files) {
        data.code_files.forEach((f) => {
          store.codeFiles[f.filename] = f.content
        })
      }
      // 任务已结束，主动断开 SSE，避免连接永久驻留
      disconnect()
    })

    es.addEventListener('trace_summary', (e: MessageEvent) => {
      const data: SSETraceSummaryData = JSON.parse(e.data)
      store._traceSummary = data
    })

    es.addEventListener('preview', (e: MessageEvent) => {
      const data: SSEPreviewData = JSON.parse(e.data)
      // 更新预览面板的验证状态指示灯
      let status: 'passed' | 'failed' | 'unavailable' = 'unavailable'
      let tooltip = ''
      if (!data.available) {
        status = 'unavailable'
        tooltip = '预览验证不可用（浏览器未安装）'
      } else if (data.passed) {
        status = 'passed'
        tooltip = '预览验证通过，无运行时错误'
      } else {
        status = 'failed'
        tooltip = `运行时错误: ${(data.errors || []).length} 个问题`
      }
      previewStore.updatePreviewStatus(status, data.errors || [], tooltip)
    })

    // ---- SDD 新增事件 ----

    es.addEventListener('spec', (e: MessageEvent) => {
      const data: SSESpecData = JSON.parse(e.data)
      store._specData = data
      // 标记紧随其后的那条 TL 分析文本消息：它与计划卡是**同一份内容**
      // （requirement_restated + features + acceptance_criteria 逐字相同），
      // 只是卡片的纯文本副本。不标记的话，用户要把同一份计划读两遍
      // （浮层卡片一遍 + 文本消息一遍），刷新后还会叠加已确认卡片变成三遍。
      // 标记后由对话流统一只渲染卡片（见 DialoguePanel 的 messages 计算）。
      //
      // 只在"首次出计划"时打标：已确认状态下的 spec 重放不该再改动消息属性。
      // 刷新恢复那条链路的同名消息自带 plan 字段（来自 DB），不依赖这里。
      const _list = store.dialogueMessages
      const _last: any = _list[_list.length - 1]
      if (_last && _last.role === 'agent' && !_last.plan && !_last.plan_confirmed) {
        _last.has_plan = true
      }
      // 记录 TL 分析消息的插入位置（spec 事件到达时，TL 消息已通过 dialogue 事件
      // 追加到消息列表末尾，length 即它之后一位 —— 确认卡片落在分析结果之后，
      // 与后端落库位置一致）
      store._specInsertIndex = _list.length
      // 如果已经确认过，不要覆盖为 needs_confirmation（刷新页面 SSE 重连时可能重放）
      if (store.planStatus !== 'confirmed') {
        store.planStatus = 'needs_confirmation'
      }
      // spec 事件到达 = TL 已完成，后端随即把需求置为 planning 等用户确认。
      // 但前端的 status 是 API 快照，不会随 SSE 更新；不同步就会出现
      // 「确认卡片已经在眼前、顶部却还写着准备中」的错位（req 165 实测）。
      const _req = store.currentRequirement
      if (_req && (_req.status === 'pending' || _req.status === 'processing')) {
        _req.status = 'planning'
      }
      // 确认卡片出现 = 流程停在选择点，绝不在"生成中"。
      // SSE 会整段回放缓冲消息，其中包含上一轮的 progress 事件——若不在这里清掉，
      // 界面会一边让你确认计划、一边亮着上一轮残留的「编码」阶段灯（req 164）。
      if (store.planStatus === 'needs_confirmation') {
        store.isGenerating = false
        store.progress = emptyProgress()
        store.serverElapsedS = 0
      }
    })

    es.addEventListener('task_list', (e: MessageEvent) => {
      const data: SSETaskListData = JSON.parse(e.data)
      // 重连时后端会回放缓冲里的 task_list（推送时状态还是 pending）。
      // 直接采信会把已结束需求的进度倒退回"全部待处理"，与"全部已完成"一样是假象，
      // 所以先过一遍产物对账（进行中的需求不受影响，对账会原样返回）。
      store._taskList = store.reconcileTaskList(data.tasks || [])
    })

    es.addEventListener('task_update', (e: MessageEvent) => {
      const data: SSETaskUpdateData = JSON.parse(e.data)
      const tasks = store._taskList || []
      const found = tasks.find((t) => t.file === data.file)
      if (found) {
        found.status = data.status
      }
    })

    es.addEventListener('evaluator_result', (e: MessageEvent) => {
      const data: SSEEvaluatorResultData = JSON.parse(e.data)
      store.evaluatorResult = data
    })

    es.addEventListener('checklist_update', (e: MessageEvent) => {
      const data: SSEChecklistUpdateData = JSON.parse(e.data)
      const spec = store._specData
      if (spec?.acceptance_criteria) {
        const ac = spec.acceptance_criteria.find((a) => a.id === data.ac_id)
        if (ac) {
          ac.passed = data.passed
          ac.reason = data.reason || ''
          // P4: 四态信号优先用后端 state；缺省时回退到 passed 布尔推导
          if (data.state) ac.state = data.state
        }
      }
    })

    es.addEventListener('cancelled', (_e: MessageEvent) => {
      store.isGenerating = false
      store.progress = emptyProgress()
      store.addDialogueMessage({
        role: 'system',
        name: 'System',
        content: '操作已被用户取消',
      })
      // 任务已取消，主动断开 SSE
      disconnect()
    })

    es.addEventListener('error', (e: MessageEvent) => {
      try {
        const data: SSEErrorData = JSON.parse(e.data)
        store.addDialogueMessage({
          role: 'system',
          content: `错误: ${data.message}`,
        })
      } catch {
        // ignore parse errors
      }
    })

    es.onerror = () => {
      // 瞬时断线时保持 isGenerating 不变（避免后端仍在生成、前端却误判为可再次提交）
      isConnected.value = false
      // 关闭 EventSource，禁用其内置自动重连，改由下方指数退避调度
      es.close()
      if (eventSource.value === es) eventSource.value = null
      scheduleReconnect(epoch)
    }
  }

  // 探测是否鉴权失效（EventSource 拿不到 HTTP 状态码，用受保护接口判断）
  async function probeAuth(): Promise<boolean> {
    try {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 3000)
      const resp = await fetch('/api/user/info', {
        credentials: 'include',
        signal: controller.signal,
      })
      clearTimeout(timer)
      return resp.status !== 401
    } catch {
      // 网络异常无法判断，按临时故障处理，继续退避重试
      return true
    }
  }

  async function scheduleReconnect(epoch: number) {
    if (reconnectTimer) return

    // 首次失败先探测鉴权：cookie 失效（401）时立即停止并引导登录
    if (retryCount === 0) {
      const authed = await probeAuth()
      // 探测期间组件已卸载或已建立新连接，放弃本次调度
      if (stopped || connectEpoch !== epoch) return
      if (!authed) {
        connectionError.value = '登录已过期，请重新登录'
        authStore.clearAuth()
        router.push('/login')
        return
      }
    }

    if (retryCount >= MAX_RETRIES) {
      connectionError.value = '实时连接失败，已停止自动重连'
      return
    }

    const delay = Math.min(retryDelay, MAX_RETRY_DELAY)
    retryDelay *= 2
    retryCount++
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  function disconnect() {
    stopped = true
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (eventSource.value) {
      eventSource.value.close()
      eventSource.value = null
    }
    isConnected.value = false
  }

  onUnmounted(disconnect)

  return {
    isConnected,
    connectionError,
    serverElapsedS,
    lastHeartbeatAt,
    connect,
    disconnect,
  }
}
