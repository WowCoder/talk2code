import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import type {
  Requirement,
  DialogueMessage,
  CodeFile,
} from '@/types/api'
import type { SSEQuestionFormData, SSEEvaluatorResultData, SSESpecData, SSETraceSummaryData } from '@/types/sse'
import { useAuthStore } from './auth'

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
  // Hook 检查结果（仅存储当前迭代的失败项，每次 iteration_batch 时清除旧数据）
  const hookChecks = ref<Array<{ hook_name: string; passed: boolean; message: string }>>([])
  // SPEC 和 Task 数据（从 dialogue_history 的 plan 字段恢复，或 SSE 推送）
  const _specData = ref<SSESpecData | null>(null)
  const _taskList = ref<any[]>([])
  // Plan 确认状态: null=无plan, 'needs_confirmation'=等待确认, 'confirmed'=已确认
  const planStatus = ref<'needs_confirmation' | 'confirmed' | null>(null)
  // Trace 总结数据
  const _traceSummary = ref<SSETraceSummaryData | null>(null)

  // ===== Actions =====
  async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
    const authStore = useAuthStore()
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...authStore.getAuthHeaders(),
      ...(options.headers as Record<string, string> || {}),
    }
    const response = await fetch(url, { ...options, headers })
    if (!response.ok) {
      // 401 → token 过期/无效 → 清除登录态并跳转
      if (response.status === 401) {
        authStore.logout()
        window.location.href = '/login'
        throw new Error('未登录或登录已过期')
      }
      // 429 → 限流，给出友好提示
      if (response.status === 429) {
        const err = await response.json().catch(() => ({}))
        const retryAfter = err.retry_after || '若干秒'
        throw new Error(`操作太频繁，请${retryAfter === 'None' ? '稍后' : retryAfter + '秒后'}再试`)
      }
      const err = await response.json().catch(() => ({ error: 'Network error' }))
      throw new Error(err.error || `HTTP ${response.status}`)
    }
    return response.json()
  }

  async function loadRequirement(id: number): Promise<{ requirement: Requirement; trace?: any }> {
    // 先重置所有状态，避免旧需求数据残留
    dialogueMessages.value = []
    Object.keys(codeFiles).forEach((k) => delete codeFiles[k])
    questionForm.value = null
    evaluatorResult.value = null
    _specData.value = null
    _taskList.value = []
    planStatus.value = null
    _traceSummary.value = null
    hookChecks.value = []

    const data = await api<{ requirement: Requirement; trace?: any }>(`/api/requirements/${id}`)
    currentRequirement.value = data.requirement

    // Restore dialogue
    if (data.requirement.dialogue_history?.length) {
      dialogueMessages.value = data.requirement.dialogue_history

      // 恢复 question_form：仅当 pending 状态且表单未被提交过
      if (data.requirement.status === 'pending') {
        for (const msg of data.requirement.dialogue_history) {
          if ((msg as any).question_form) {
            const qf = (msg as any).question_form
            // 如果后端已标记 submitted，说明用户已经提交过，不再恢复可编辑表单
            if (!qf.submitted) {
              questionForm.value = qf
            } else {
              // 已提交：仅保留一份只读展示（answers 已在后端注入）
              questionForm.value = { ...qf, submitted: true }
            }
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
            status: 'completed',
          }))
          break
        }
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
    // Deduplicate
    const exists = dialogueMessages.value.some(
      (m) => m.content === msg.content && m.role === msg.role
    )
    if (exists) return
    dialogueMessages.value.push(msg)
    // Keep last 100 messages
    if (dialogueMessages.value.length > 200) {
      dialogueMessages.value = dialogueMessages.value.slice(-100)
    }
  }

  function addHookCheck(check: { hook_name: string; passed: boolean; message: string }) {
    // 避免重复添加相同 hook 的检查结果
    const exists = hookChecks.value.some(h => h.hook_name === check.hook_name && h.message === check.message)
    if (!exists) {
      hookChecks.value.push(check)
    }
  }

  function clearHookChecks() {
    hookChecks.value = []
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

  async function sendChatMessage(message: string) {
    if (!currentRequirement.value) return null
    const data = await api<{
      needs_clarification?: boolean
      question_form?: SSEQuestionFormData
      dialogue_history?: DialogueMessage[]
      code_files?: CodeFile[]
      updated_files?: string[]
    }>(`/api/requirements/${currentRequirement.value.id}/chat`, {
      method: 'POST',
      body: JSON.stringify({ message }),
    })

    // 如果后端返回澄清需求，只更新对话历史，不更新代码文件
    if (data.needs_clarification) {
      if (data.dialogue_history?.length) {
        dialogueMessages.value = data.dialogue_history
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
          dialogueMessages.value.push(msg as DialogueMessage)
          existingKeys.add(key)
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

  async function sendChatClarification(answers: Record<string, string>) {
    /** 将澄清答案拼接到原始消息后，重新发送 chat 请求 */
    if (!pendingChatClarification.value || !currentRequirement.value) return

    const { originalMessage } = pendingChatClarification.value
    pendingChatClarification.value = null
    questionForm.value = null

    // 拼接答案
    const answerText = Object.entries(answers)
      .filter(([, v]) => v)
      .map(([q, a]) => `${q}: ${a}`)
      .join('；')

    const enrichedMessage = `[用户补充说明]\n${answerText}\n\n原始修改意见：${originalMessage}`

    // 将用户答案作为对话消息展示
    addDialogueMessage({
      role: 'user',
      name: '用户',
      content: answerText || '已确认',
    })

    // 重新发送
    return sendChatMessage(enrichedMessage)
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
    currentRequirement.value = null
    dialogueMessages.value = []
    Object.keys(codeFiles).forEach((k) => delete codeFiles[k])
    activeFile.value = 'index.html'
    isGenerating.value = false
    progress.value = { currentAgent: '', percent: 0 }
    questionForm.value = null
    pendingChatClarification.value = null
    evaluatorResult.value = null
    _specData.value = null
    _taskList.value = []
    planStatus.value = null
    _traceSummary.value = null
    hookChecks.value = []
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
    addHookCheck,
    clearHookChecks,
    hookChecks,
    updateCodeFiles,
    setActiveFile,
    saveCodeFile,
    sendChatMessage,
    sendChatClarification,
    submitClarification,
    trashRequirement,
    restoreRequirement,
    deleteRequirement,
    evaluatorResult,
    _specData,
    _taskList,
    _traceSummary,
    planStatus,
    confirmPlan,
    cancelTask,
    reset,
  }
})
