import { ref, type Ref, onUnmounted } from 'vue'
import { useRequirementStore } from '@/stores/requirement'
import { usePreviewStore } from '@/stores/preview'
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
  SSETraceSummaryData,
  SSEErrorData,
  SSEPreviewData,
  SSESpecData,
  SSETaskListData,
  SSETaskUpdateData,
  SSEChecklistUpdateData,
  SSEEvaluatorResultData,
  SSEIterationBatchData,
} from '@/types/sse'

export function useSSE(reqId: Ref<number | null>) {
  const store = useRequirementStore()
  const previewStore = usePreviewStore()
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
      // 已提交的表单不再弹出浮动编辑框（消息流中已有已提交卡片）
      if (data.submitted) return
      // SSE 消息缓冲区回放防御：如果对话中已有已提交表单，忽略回放的旧事件
      if (store.dialogueMessages.some((m: any) => m.question_form?.submitted === true)) return
      // 避免重复设置（loadRequirement 已恢复时跳过）
      if (store.questionForm) return
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
      // 新迭代开始 → 清除上一轮的 hook 检查结果
      store.clearHookChecks()
      store.addDialogueMessage({
        role: 'iteration_batch',
        name: data.coder_name || 'Agent',
        content: (data as any).content || `第 ${data.iteration} 轮迭代 — ${data.tools.length} 个操作`,
        iteration: data.iteration,
        thinking_preview: data.thinking_preview,
        agent_text: data.agent_text,
        tools: data.tools,
      })
    })

    es.addEventListener('hook_check', (e: MessageEvent) => {
      const data: SSEHookCheckData = JSON.parse(e.data)
      // hook 检查结果存入独立状态，每次迭代开始时自动清除，避免残留
      if (!data.passed) {
        store.addHookCheck({
          hook_name: data.hook_name,
          passed: data.passed,
          message: data.message || '',
        })
      }
    })

    es.addEventListener('complete', (e: MessageEvent) => {
      const data: SSECompleteData = JSON.parse(e.data)
      store.isGenerating = false
      store.progress = { currentAgent: '', percent: 100 }
      // 清除可能残留的 hook 检查结果
      store.clearHookChecks()
      if (data.code_files) {
        data.code_files.forEach((f) => {
          store.codeFiles[f.filename] = f.content
        })
      }
    })

    es.addEventListener('trace_summary', (e: MessageEvent) => {
      const data: SSETraceSummaryData = JSON.parse(e.data)
      lastTraceSummary.value = data
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
      ;(store as any)._specData = data
      // 记录 TL 分析消息的插入位置（spec 事件到达时，TL 消息已通过 dialogue 事件
      // 追加到消息列表末尾，确认卡片应插入到它前面）
      ;(store as any)._specInsertIndex = store.dialogueMessages.length
      // 如果已经确认过，不要覆盖为 needs_confirmation（刷新页面 SSE 重连时可能重放）
      if (store.planStatus !== 'confirmed') {
        store.planStatus = 'needs_confirmation'
      }
    })

    es.addEventListener('task_list', (e: MessageEvent) => {
      const data: SSETaskListData = JSON.parse(e.data)
      ;(store as any)._taskList = data.tasks || []
    })

    es.addEventListener('task_update', (e: MessageEvent) => {
      const data: SSETaskUpdateData = JSON.parse(e.data)
      const tasks = (store as any)._taskList || []
      const found = tasks.find((t: any) => t.file === data.file)
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
      const spec = (store as any)._specData
      if (spec?.acceptance_criteria) {
        const ac = spec.acceptance_criteria.find((a: any) => a.id === data.ac_id)
        if (ac) {
          ac.passed = data.passed
          ac.reason = data.reason || ''
        }
      }
    })

    es.addEventListener('cancelled', (_e: MessageEvent) => {
      store.isGenerating = false
      store.progress = { currentAgent: '', percent: 0 }
      store.clearHookChecks()
      store.addDialogueMessage({
        role: 'system',
        name: 'System',
        content: '操作已被用户取消',
      })
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
