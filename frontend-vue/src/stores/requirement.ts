import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import type {
  Requirement,
  DialogueMessage,
  CodeFile,
} from '@/types/api'
import type { SSEQuestionFormData } from '@/types/sse'
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
      const err = await response.json().catch(() => ({ error: 'Network error' }))
      throw new Error(err.error || `HTTP ${response.status}`)
    }
    return response.json()
  }

  async function loadRequirement(id: number): Promise<{ requirement: Requirement; trace?: any }> {
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
    }

    // Restore code files
    if (data.requirement.code_files?.length) {
      data.requirement.code_files.forEach((f: CodeFile) => {
        codeFiles[f.filename] = f.content
      })
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

    // 服务端响应是权威的最终状态，直接替换本地数据
    // （SSE 在请求期间已实时推送增量更新，此处确保数据与服务端一致）
    if (data.dialogue_history?.length) {
      dialogueMessages.value = data.dialogue_history
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

  async function submitPermission(decision: 'allow' | 'deny') {
    if (!currentRequirement.value) return
    await api(`/api/requirements/${currentRequirement.value.id}/permission`, {
      method: 'POST',
      body: JSON.stringify({ decision }),
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

  function reset() {
    currentRequirement.value = null
    dialogueMessages.value = []
    Object.keys(codeFiles).forEach((k) => delete codeFiles[k])
    activeFile.value = 'index.html'
    isGenerating.value = false
    progress.value = { currentAgent: '', percent: 0 }
    questionForm.value = null
    pendingChatClarification.value = null
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
    sendChatClarification,
    submitClarification,
    submitPermission,
    trashRequirement,
    restoreRequirement,
    deleteRequirement,
    reset,
  }
})
