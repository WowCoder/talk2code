import { ref, type Ref, onUnmounted } from 'vue'
import { useRequirementStore } from '@/stores/requirement'
import type {
  SSEDialogueData,
  SSECodeData,
  SSEProgressData,
  SSEQuestionFormData,
  SSEToolCallData,
  SSEToolResultData,
  SSEThinkingData,
  SSEHookCheckData,
  SSECompleteData,
  SSEPermissionData,
  SSETraceSummaryData,
  SSEErrorData,
} from '@/types/sse'

export function useSSE(reqId: Ref<number | null>) {
  const store = useRequirementStore()
  const eventSource = ref<EventSource | null>(null)
  const isConnected = ref(false)
  const lastTraceSummary = ref<SSETraceSummaryData | null>(null)

  function connect() {
    if (!reqId.value) return
    disconnect()

    const es = new EventSource(`/api/sse/${reqId.value}`)
    eventSource.value = es

    es.addEventListener('connected', () => {
      isConnected.value = true
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
      store.isGenerating = true
      store.progress = {
        currentAgent: data.current_agent,
        percent: data.progress,
      }
    })

    es.addEventListener('question-form', (e: MessageEvent) => {
      const data: SSEQuestionFormData = JSON.parse(e.data)
      store.addDialogueMessage({
        role: 'system',
        name: 'System',
        content: '__QUESTION_FORM__',
        question_form: data,
      })
      store.questionForm = data
    })

    es.addEventListener('tool_call', (e: MessageEvent) => {
      const data: SSEToolCallData = JSON.parse(e.data)
      store.addDialogueMessage({
        role: 'tool_call',
        name: data.tool_name,
        content: data.readable || data.tool_name,
        tool_name: data.tool_name,
        readable: data.readable,
        arguments: data.arguments,
      })
    })

    es.addEventListener('tool_result', (e: MessageEvent) => {
      const data: SSEToolResultData = JSON.parse(e.data)
      store.addDialogueMessage({
        role: 'tool_result',
        name: data.tool_name,
        content: data.summary || data.error || '',
        tool_name: data.tool_name,
        success: data.success,
        summary: data.summary,
        error: data.error,
      })
    })

    es.addEventListener('thinking', (e: MessageEvent) => {
      const data: SSEThinkingData = JSON.parse(e.data)
      store.addDialogueMessage({
        role: 'thinking',
        name: 'Thinking',
        content: data.content,
      })
    })

    es.addEventListener('hook_check', (e: MessageEvent) => {
      const data: SSEHookCheckData = JSON.parse(e.data)
      store.addDialogueMessage({
        role: 'hook_check',
        name: 'Hook',
        content: data.message || '',
        hook_name: data.hook_name,
        passed: data.passed,
        message: data.message,
      })
    })

    es.addEventListener('complete', (e: MessageEvent) => {
      const data: SSECompleteData = JSON.parse(e.data)
      store.isGenerating = false
      store.progress = { currentAgent: '', percent: 100 }
      if (data.code_files) {
        data.code_files.forEach((f) => {
          store.codeFiles[f.filename] = f.content
        })
      }
    })

    es.addEventListener('permission_request', (e: MessageEvent) => {
      const data: SSEPermissionData = JSON.parse(e.data)
      ;(store as any)._permissionRequest = {
        ...data,
        timestamp: Date.now(),
      }
    })

    es.addEventListener('trace_summary', (e: MessageEvent) => {
      const data: SSETraceSummaryData = JSON.parse(e.data)
      lastTraceSummary.value = data
      ;(store as any)._traceSummary = data
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
      isConnected.value = false
      store.isGenerating = false
    }
  }

  function disconnect() {
    if (eventSource.value) {
      eventSource.value.close()
      eventSource.value = null
    }
    isConnected.value = false
  }

  onUnmounted(disconnect)

  return {
    isConnected,
    lastTraceSummary,
    connect,
    disconnect,
  }
}
