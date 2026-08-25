import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import type {
  Requirement,
  DialogueMessage,
  CodeFile,
} from '@/types/api'
import type { SSEQuestionFormData, SSEEvaluatorResultData, SSESpecData, SSETraceSummaryData, SSETask } from '@/types/sse'
import { useApi } from '@/composables/useApi'

export const useRequirementStore = defineStore('requirement', () => {
  // ===== State =====
  const currentRequirement = ref<Requirement | null>(null)
  const dialogueMessages = ref<DialogueMessage[]>([])
  const codeFiles = reactive<Record<string, string>>({})
  const activeFile = ref<string>('index.html')
  const isGenerating = ref(false)
  const progress = ref({ currentAgent: '', percent: 0 })
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

  function messageKey(msg: DialogueMessage): string {
    // SSE dialogue 事件携带时间戳，重放时同一事件的时间戳一致，可作幂等键；
    // 迭代批量事件用 iteration 序号去重；无时间戳的本地消息（如用户连发"继续"）不去重
    if (msg.timestamp) {
      return `ts::${msg.role}::${msg.name || ''}::${msg.content}::${msg.timestamp}`
    }
    if (msg.role === 'iteration_batch' && msg.iteration !== undefined) {
      return `iter::${msg.iteration}`
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
          _taskList.value = (plan.implementation_order || []).map((f: string) => ({
            file: f,
            description: f,
            status: 'completed' as const,
          }))
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
  }

  async function cancelTask(): Promise<void> {
    if (!currentRequirement.value) return
    await api(`/api/requirements/${currentRequirement.value.id}/cancel`, { method: 'POST' })
    isGenerating.value = false
    progress.value = { currentAgent: '', percent: 0 }
  }

  function reset() {
    loadSeq++ // 使进行中的旧 loadRequirement 失效，避免竞态写入
    currentRequirement.value = null
    dialogueMessages.value = []
    seenMessageKeys.clear()
    Object.keys(codeFiles).forEach((k) => delete codeFiles[k])
    activeFile.value = 'index.html'
    isGenerating.value = false
    progress.value = { currentAgent: '', percent: 0 }
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
    reset,
  }
})
