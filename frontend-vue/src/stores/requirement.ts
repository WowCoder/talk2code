import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import type {
  Requirement,
  DialogueMessage,
  CodeFile,
} from '@/types/api'
import { useAuthStore } from './auth'

export const useRequirementStore = defineStore('requirement', () => {
  // ===== State =====
  const currentRequirement = ref<Requirement | null>(null)
  const dialogueMessages = ref<DialogueMessage[]>([])
  const codeFiles = reactive<Record<string, string>>({})
  const activeFile = ref<string>('index.html')
  const isGenerating = ref(false)
  const progress = ref({ currentAgent: '', percent: 0 })

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
    if (!currentRequirement.value) return
    const data = await api<{
      dialogue_history: DialogueMessage[]
      code_files: CodeFile[]
    }>(`/api/requirements/${currentRequirement.value.id}/chat`, {
      method: 'POST',
      body: JSON.stringify({ message }),
    })

    if (data.dialogue_history) {
      dialogueMessages.value = data.dialogue_history
    }
    if (data.code_files) {
      // Reset code files from chat response
      Object.keys(codeFiles).forEach((k) => delete codeFiles[k])
      data.code_files.forEach((f: CodeFile) => {
        codeFiles[f.filename] = f.content
      })
    }
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

  function reset() {
    currentRequirement.value = null
    dialogueMessages.value = []
    Object.keys(codeFiles).forEach((k) => delete codeFiles[k])
    activeFile.value = 'index.html'
    isGenerating.value = false
    progress.value = { currentAgent: '', percent: 0 }
  }

  return {
    currentRequirement,
    dialogueMessages,
    codeFiles,
    activeFile,
    isGenerating,
    progress,
    loadRequirement,
    addDialogueMessage,
    updateCodeFiles,
    setActiveFile,
    saveCodeFile,
    sendChatMessage,
    submitClarification,
    submitPermission,
    trashRequirement,
    restoreRequirement,
    deleteRequirement,
    reset,
  }
})
