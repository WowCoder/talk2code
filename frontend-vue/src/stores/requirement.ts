import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import type {
  Requirement,
  DialogueMessage,
  CodeFile,
} from '@/types/api'
import type { SSEQuestionFormData, SSEEvaluatorResultData, SSESpecData, SSETraceSummaryData, SSETask } from '@/types/sse'
import { useApi } from '@/composables/useApi'

/** 执行进度：currentAgent 承载"当前在做什么"的动作描述（后端已推动作而非角色名） */
export interface ProgressState {
  currentAgent: string
  percent: number
  /** 阶段：planning / coding / verifying / repairing，'' 表示未知 */
  stage: string
  /** 最后一条进度到达的本地时间戳（ms），用于计算"当前动作已持续多久" */
  updatedAt: number
}

export function emptyProgress(): ProgressState {
  return { currentAgent: '', percent: 0, stage: '', updatedAt: 0 }
}

export const useRequirementStore = defineStore('requirement', () => {
  // ===== State =====
  const currentRequirement = ref<Requirement | null>(null)
  const dialogueMessages = ref<DialogueMessage[]>([])
  const codeFiles = reactive<Record<string, string>>({})
  const activeFile = ref<string>('index.html')
  const isGenerating = ref(false)
  const progress = ref<ProgressState>(emptyProgress())
  // 服务端心跳累计秒数：LLM 静默期的唯一活性信号，供 UI 显示"已等待 Ns"
  const serverElapsedS = ref(0)
  const questionForm = ref<SSEQuestionFormData | null>(null)
  // chat 模式下的澄清上下文（暂存原始消息，表单提交后拼接重新发送）
  const pendingChatClarification = ref<{ originalMessage: string } | null>(null)
  // Evaluator 评估结果
  const evaluatorResult = ref<SSEEvaluatorResultData | null>(null)
  // SPEC 和 Task 数据（从 dialogue_history 的 plan 字段恢复，或 SSE 推送）
  const _specData = ref<SSESpecData | null>(null)
  const _taskList = ref<SSETask[]>([])
  // spec 事件到达时记录确认卡片应插入的对话位置（TL 分析消息之前）
  const _specInsertIndex = ref<number | null>(null)
  // Plan 确认状态: null=无plan, 'needs_confirmation'=等待确认, 'confirmed'=已确认
  const planStatus = ref<'needs_confirmation' | 'confirmed' | null>(null)
  // Trace 总结数据
  const _traceSummary = ref<SSETraceSummaryData | null>(null)

  // ===== 竞态 / 幂等去重（会话内，非响应式）=====
  // 递增请求序号：只有最新一次 loadRequirement 的结果允许写入 state
  let loadSeq = 0
  // 已入列消息的幂等键集合（防 SSE 重连重放整段历史导致重复入列）
  const seenMessageKeys = new Set<string>()

  // ===== API (from shared composable) =====
  const { api } = useApi()

/**
 * 从「计划」+「实际产物」推导任务状态。
 *
 * 这里曾经把 plan.implementation_order 的每个文件一律标成 'completed'——只要计划里
 * 写了这个文件就宣称完成，从不核对磁盘上到底有没有产物。需求 162 在 Coder 阶段 LLM
 * 读超时失败、code_files 为空，任务面板照样显示「6/6 完成」，用户据此以为代码好了，
 * 于是追问「代码 TAB 怎么是空的、发布按钮为什么能点」。任务面板存在的意义是让用户
 * 知道真实进度，所以状态必须由产物对账得出，不能由计划单方面宣称。
 */
function deriveTaskList(plan: any, requirement: Requirement): SSETask[] {
  const planTasks: any[] = Array.isArray(plan?.tasks) ? plan.tasks : []
  // implementation_order 是权威顺序，缺失时退回 tasks 里声明的文件
  const order: string[] = plan?.implementation_order?.length
    ? plan.implementation_order
    : planTasks.map((t) => t?.file).filter(Boolean)
  const descByFile = new Map<string, string>()
  for (const t of planTasks) {
    if (t?.file && t?.description) descByFile.set(t.file, t.description)
  }

  const produced = new Set((requirement.code_files || []).map((f) => f.filename))
  // 需求已经终止（成功 / 失败 / 待用户处理）后不会再有新的写入。此时仍未产出的文件
  // 不是「还没轮到」，而是「没写出来」——标 failed 而不是 pending，否则失败的需求会
  // 留下一列永远停在"待处理"的任务，看起来像还有希望（162 就是这个观感）。
  // interrupted 仍可续跑，算未完成。
  const settled = !['pending', 'planning', 'processing', 'interrupted'].includes(requirement.status)

  return order.map((file) => ({
    file,
    description: descByFile.get(file) || file,
    status: produced.has(file) ? 'completed' : settled ? 'failed' : 'pending',
  }))
}

/**
 * 对实时推送来的任务列表做一次对账。
 *
 * 后端推送 task_list 时状态一律是 pending（TL 刚出计划，还没写代码，本该如此）。
 * 但 sse_manager 会把广播过的消息放进缓冲区，客户端重连时整段回放——
 * 于是刷新一个已经结束的需求，会收到一份"全部待处理"的历史快照，把真实进度
 * 倒退回去。失败的需求显示 6/6 待处理，和显示 6/6 完成一样是假象。
 * 所以只在进行中的需求上信任推送值；已终止的按产物重新定状态。
 */
function reconcileTaskList(tasks: SSETask[]): SSETask[] {
  const req = currentRequirement.value
  if (!req) return tasks
  const settled = !['pending', 'planning', 'processing', 'interrupted'].includes(req.status)
  if (!settled) return tasks
  const produced = new Set((req.code_files || []).map((f) => f.filename))
  return tasks.map((t) => ({
    ...t,
    status: produced.has(t.file) ? 'completed' : 'failed',
  }))
}

function messageKey(msg: DialogueMessage): string {
  // SSE dialogue 事件携带时间戳，重放时同一事件的时间戳一致，可作幂等键；
  // 无时间戳的本地消息（如用户连发"继续"）不去重
  if (msg.timestamp) {
    return `ts::${msg.role}::${msg.name || ''}::${msg.content}::${msg.timestamp}`
  }
  if (msg.role === 'iteration_batch') {
    // 必须带上 content：需求被「重跑」时两组迭代都从第 1 轮开始，
    // 若只用 iter::N 会跨轮次碰撞，把新一轮卡片误判为重复而丢弃/错位。
    // 同一轮次的精确重放（content 相同）仍可正常去重。
    return `iter::${msg.iteration ?? ''}::${msg.content ?? ''}`
  }
  if (msg.role === 'hook_check') {
    // hook_check 事件没有时间戳，必须按内容去重，否则 SSE 重连/回放时会反复入列，
    // 表现为「一直收到同一条消息」
    return `${msg.role}::${msg.name || ''}::${msg.content ?? ''}`
  }
  // 无时间戳的 agent 消息（旧版本落库的 TL 分析等）用内容做兜底键：
  // 同一消息在「历史恢复 + SSE 回放」两条路径下都无时间戳，不去重就会重复显示
  // （req 164：TL 分析出现 4 遍）。只对 agent 侧生效——用户连发相同消息
  // （如连续「继续」）是合法行为，绝不能被这里吞掉。
  if (msg.role !== 'user' && msg.content) {
    return `content::${msg.role}::${msg.name || ''}::${msg.content}`
  }
  return ''
}

  async function loadRequirement(id: number): Promise<{ requirement: Requirement; trace?: any; evaluator?: SSEEvaluatorResultData } | null> {
    const seq = ++loadSeq
    // 先重置所有状态，避免旧需求数据残留
    dialogueMessages.value = []
    seenMessageKeys.clear()
    Object.keys(codeFiles).forEach((k) => delete codeFiles[k])
    activeFile.value = 'index.html'
    progress.value = emptyProgress()
    serverElapsedS.value = 0
    questionForm.value = null
    evaluatorResult.value = null
    _specData.value = null
    _taskList.value = []
    _specInsertIndex.value = null
    planStatus.value = null
    _traceSummary.value = null

    const data = await api<{ requirement: Requirement; trace?: any; evaluator?: SSEEvaluatorResultData }>(`/api/requirements/${id}`)

    // 竞态保护：期间又发起了新的 loadRequirement，本次结果作废
    if (seq !== loadSeq) return null

    currentRequirement.value = data.requirement

    // 恢复 trace（页面刷新后恢复 token/成本统计）
    if (data.trace) {
      _traceSummary.value = data.trace
    }

    // 恢复 evaluator 评估结果（页面刷新后恢复评分展示）
    if (data.evaluator) {
      evaluatorResult.value = data.evaluator
    }

    // Restore dialogue
    if (data.requirement.dialogue_history?.length) {
      dialogueMessages.value = data.requirement.dialogue_history

      // 恢复 question_form：仅当 pending 状态且存在未提交的表单
      // （已提交的表单以带 question_form.submitted 的 user 消息形式在消息流中渲染）
      if (data.requirement.status === 'pending') {
        for (const msg of data.requirement.dialogue_history) {
          const qf = (msg as any).question_form
          if (qf && !qf.submitted) {
            questionForm.value = qf
            break
          }
        }
      }

      // 从 TL 消息中恢复 SPEC 和 Task 数据（页面刷新后可用）
      for (const msg of data.requirement.dialogue_history) {
        if ((msg as any).plan) {
          const plan = (msg as any).plan
          _specData.value = {
            title: data.requirement.title,
            acceptance_criteria: plan.acceptance_criteria || [],
            file_structure: plan.file_structure || [],
            tech_stack: plan.tech_stack || {},
          }
          _taskList.value = deriveTaskList(plan, data.requirement)
          break
        }
      }

      // 建立幂等键集合，避免 SSE 重连重放整段历史时重复入列
      for (const m of data.requirement.dialogue_history) {
        const k = messageKey(m)
        if (k) seenMessageKeys.add(k)
      }
    }

    // Restore code files
    if (data.requirement.code_files?.length) {
      data.requirement.code_files.forEach((f: CodeFile) => {
        codeFiles[f.filename] = f.content
      })
    }

    // 恢复 Plan 确认状态（从后端 API 返回的 plan_status 字段）
    const planStatusFromApi = (data.requirement as any).plan_status
    if (planStatusFromApi === 'needs_confirmation') {
      planStatus.value = 'needs_confirmation'
    } else if (planStatusFromApi === 'confirmed') {
      planStatus.value = 'confirmed'
    }

    return data
  }

  function addDialogueMessage(msg: DialogueMessage) {
    // 幂等去重（SSE 重连重放防御）：同一事件（时间戳/迭代号相同）只入列一次，
    // 覆盖整段历史重放的场景；无时间戳的本地消息（如用户连发"继续"）不去重
    const key = messageKey(msg)
    if (key && seenMessageKeys.has(key)) return

    // 仅去重「连续」相同的消息（兼容旧版后端无时间戳的重复推送），
    // 不全局按 role+content 去重——否则用户连发"继续"等相同内容会被误删
    const last = dialogueMessages.value[dialogueMessages.value.length - 1]
    const isConsecutiveDup =
      !!last &&
      last.role === msg.role &&
      last.content === msg.content &&
      last.name === msg.name
    if (isConsecutiveDup) return

    if (key) seenMessageKeys.add(key)
    dialogueMessages.value.push(msg)
    // Keep last 100 messages
    if (dialogueMessages.value.length > 200) {
      dialogueMessages.value = dialogueMessages.value.slice(-100)
    }
  }

  function updateCodeFiles(data: { filename?: string; content?: string; files?: Array<{ filename: string; content: string }> }) {
    if (data.files) {
      data.files.forEach((f) => {
        codeFiles[f.filename] = f.content
      })
    } else if (data.filename) {
      codeFiles[data.filename] = data.content || ''
    }
  }

  function setActiveFile(filename: string) {
    activeFile.value = filename
  }

  async function saveCodeFile(filename: string, content: string) {
    if (!currentRequirement.value) return
    await api(`/api/requirements/${currentRequirement.value.id}/code`, {
      method: 'POST',
      body: JSON.stringify({ filename, content }),
    })
  }

  async function sendChatMessage(
    message: string,
    clarify?: { questions: SSEQuestionFormData['questions']; answers: Record<string, string> }
  ) {
    if (!currentRequirement.value) return null
    const data = await api<{
      needs_clarification?: boolean
      question_form?: SSEQuestionFormData
      dialogue_history?: DialogueMessage[]
      code_files?: CodeFile[]
      updated_files?: string[]
    }>(`/api/requirements/${currentRequirement.value.id}/chat`, {
      method: 'POST',
      body: JSON.stringify(clarify ? { message, clarify } : { message }),
    })

    // 如果后端返回澄清需求，只更新对话历史，不更新代码文件
    if (data.needs_clarification) {
      if (data.dialogue_history?.length) {
        dialogueMessages.value = data.dialogue_history
        for (const m of data.dialogue_history) {
          const k = messageKey(m)
          if (k) seenMessageKeys.add(k)
        }
      }
      if (data.question_form) {
        questionForm.value = data.question_form
      }
      return data
    }

    // 服务端响应是权威的最终状态，合并新消息而非直接替换
    // （避免覆盖用户刚发送的本地消息和 SSE 实时推送的增量数据）
    if (data.dialogue_history?.length) {
      const existingKeys = new Set(
        dialogueMessages.value.map(m => `${m.role}::${m.content}`.slice(0, 120))
      )
      for (const msg of data.dialogue_history) {
        const key = `${(msg as any).role || 'agent'}::${(msg as any).content || ''}`.slice(0, 120)
        if (!existingKeys.has(key)) {
          const typed = msg as DialogueMessage
          dialogueMessages.value.push(typed)
          existingKeys.add(key)
          const mk = messageKey(typed)
          if (mk) seenMessageKeys.add(mk)
        }
      }
    }
    if (data.code_files) {
      Object.keys(codeFiles).forEach((k) => delete codeFiles[k])
      data.code_files.forEach((f: CodeFile) => {
        codeFiles[f.filename] = f.content
      })
    }
    return data
  }

  async function submitClarification(answers: Record<string, string>) {
    if (!currentRequirement.value) return
    await api(`/api/requirements/${currentRequirement.value.id}/clarify`, {
      method: 'POST',
      body: JSON.stringify({ answers }),
    })
  }

  async function trashRequirement(id: number) {
    await api(`/api/requirements/${id}/trash`, { method: 'PUT' })
  }

  async function restoreRequirement(id: number) {
    await api(`/api/requirements/${id}/restore`, { method: 'PUT' })
  }

  async function deleteRequirement(id: number) {
    await api(`/api/requirements/${id}`, { method: 'DELETE' })
  }

  async function confirmPlan(feedback: string = ''): Promise<void> {
    if (!currentRequirement.value) return
    await api(`/api/requirements/${currentRequirement.value.id}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ feedback }),
    })
    // 无反馈直接确认时设为 confirmed；有反馈时等待 SSE 重新推送 spec 后再确认
    if (!feedback) {
      planStatus.value = 'confirmed'
    }
    // 后端此时已把需求置为 processing 并开始编码，但前端 status 是 API 快照、
    // 不会随 SSE 更新。不同步的话：进度事件会被"已停在选择点"的判据挡掉，
    // 表现为点了确认之后界面毫无反应（req 166 实测点了确认 80 秒没动静）。
    if (currentRequirement.value) {
      currentRequirement.value.status = 'processing'
    }
    isGenerating.value = true
  }

  async function cancelTask(): Promise<void> {
    if (!currentRequirement.value) return
    await api(`/api/requirements/${currentRequirement.value.id}/cancel`, { method: 'POST' })
    isGenerating.value = false
    progress.value = emptyProgress()
    serverElapsedS.value = 0
  }

  // 续跑被中断的需求（服务重启 / 取消后重来）：后端经检查点恢复上下文
  async function resumeRequirement(): Promise<void> {
    if (!currentRequirement.value) return
    await api(`/api/requirements/${currentRequirement.value.id}/resume`, { method: 'POST' })
    // 同 confirmPlan：同步 status 快照，否则后续进度事件会被判据挡掉
    if (currentRequirement.value) {
      currentRequirement.value.status = 'processing'
    }
    isGenerating.value = true
    progress.value = emptyProgress()
    serverElapsedS.value = 0
  }

  function reset() {
    loadSeq++ // 使进行中的旧 loadRequirement 失效，避免竞态写入
    currentRequirement.value = null
    dialogueMessages.value = []
    seenMessageKeys.clear()
    Object.keys(codeFiles).forEach((k) => delete codeFiles[k])
    activeFile.value = 'index.html'
    isGenerating.value = false
    progress.value = emptyProgress()
    serverElapsedS.value = 0
    questionForm.value = null
    pendingChatClarification.value = null
    evaluatorResult.value = null
    _specData.value = null
    _taskList.value = []
    _specInsertIndex.value = null
    planStatus.value = null
    _traceSummary.value = null
  }

  return {
    currentRequirement,
    dialogueMessages,
    codeFiles,
    activeFile,
    isGenerating,
    progress,
    serverElapsedS,
    questionForm,
    pendingChatClarification,
    loadRequirement,
    addDialogueMessage,
    updateCodeFiles,
    setActiveFile,
    saveCodeFile,
    sendChatMessage,
    submitClarification,
    trashRequirement,
    restoreRequirement,
    deleteRequirement,
    evaluatorResult,
    _specData,
    _taskList,
    _specInsertIndex,
    _traceSummary,
    planStatus,
    confirmPlan,
    cancelTask,
    resumeRequirement,
    reset,
    reconcileTaskList,
  }
})
