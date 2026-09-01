<template>
  <div class="dialogue-panel">
    <div class="dialogue-header">AI 对话</div>
    <div class="dialogue-body" ref="bodyRef">
      <template v-for="(msg, i) in messages" :key="i">
        <DialogueMessage :msg="msg" />
      </template>

      <!-- Plan 确认卡片（TL 完成后展示） -->
      <PlanConfirmation
        v-if="showPlanConfirmation"
        :spec-data="specData"
        @confirmed="onPlanConfirmed"
      />

      <!-- Dynamic components from SSE（仅未提交的表单浮动展示；已提交的以消息形式在上方消息流中渲染） -->
      <QuestionForm
        v-if="questionForm && !questionForm.submitted && !showPlanConfirmation"
        :form-data="questionForm"
        :mode="questionFormMode"
        @submitted="onQuestionSubmitted"
      />
      <ExecutionPanel :trace-data="traceSummary" />

      <!-- Loading bar -->
      <div v-if="isLoading" class="loading-bar">
        <div class="lb-dots">
          <span class="lb-dot"></span>
          <span class="lb-dot"></span>
          <span class="lb-dot"></span>
        </div>
        <span class="lb-text">AI 正在处理…</span>
      </div>
    </div>
    <DialogueInput
      :disabled="isLoading"
      @send="emit('send-message', $event)"
      @stop="emit('stop')"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { useRequirementStore } from '@/stores/requirement'
import DialogueMessage from './DialogueMessage.vue'
import DialogueInput from './DialogueInput.vue'
import QuestionForm from './QuestionForm.vue'
import ExecutionPanel from './ExecutionPanel.vue'
import PlanConfirmation from './PlanConfirmation.vue'
import type { DialogueMessage as DialogueMessageType } from '@/types/api'
import type { SSEQuestionFormData, SSETraceSummaryData } from '@/types/sse'

const emit = defineEmits<{
  (
    e: 'send-message',
    message: string,
    clarify?: { questions: SSEQuestionFormData['questions']; answers: Record<string, string> }
  ): void
  (e: 'stop'): void
}>()

const bodyRef = ref<HTMLElement | null>(null)
const store = useRequirementStore()

const messages = computed(() => {
  const raw = store.dialogueMessages.filter(
    (m: DialogueMessageType) =>
      m.content !== '__QUESTION_FORM__' && !(m as any).hidden && !(m as any).plan_feedback &&
      // 「0 个操作」幽灵卡片兜底：无论消息从哪条路径进入 store（SSE 实时推送、
      // 历史恢复、chat 响应合并），只要迭代批次没有操作列表就不渲染。
      // useSSE 与后端 sse_reporter 已在源头过滤，这里是最后一条防线。
      !(m.role === 'iteration_batch' && !((m as any).tools?.length))
  )

  // 合并连续的 tool_call 消息为一个可展开组（兼容旧版后端/页面刷新时的历史数据）
  const grouped: DialogueMessageType[] = []
  let toolBatch: DialogueMessageType[] = []

  for (const msg of raw) {
    if (msg.role === 'tool_call') {
      toolBatch.push(msg)
    } else {
      if (toolBatch.length > 0) {
        if (toolBatch.length === 1) {
          grouped.push(toolBatch[0])
        } else {
          grouped.push({
            role: 'tool_call',
            name: '工具调用',
            content: '',
            _grouped: true,
            label: `📝 工具调用`,
            items: toolBatch,
          } as any)
        }
        toolBatch = []
      }
      grouped.push(msg)
    }
  }

  // 尾部残余
  if (toolBatch.length > 0) {
    if (toolBatch.length === 1) {
      grouped.push(toolBatch[0])
    } else {
      grouped.push({
        role: 'tool_call',
        name: '工具调用',
        content: '',
        _grouped: true,
        label: `📝 工具调用`,
        items: toolBatch,
      } as any)
    }
  }

  return grouped
})
const isLoading = computed(() => store.isGenerating)

// Access SSE-triggered state from the store
const questionForm = computed(() => store.questionForm)
const questionFormMode = computed(() =>
  store.pendingChatClarification ? 'chat' : 'requirement'
)
const traceSummary = computed(() => store._traceSummary as SSETraceSummaryData | null)

// Plan confirmation
const showPlanConfirmation = computed(() => store.planStatus === 'needs_confirmation')
const specData = computed(() => store._specData)
function onPlanConfirmed(feedback: string) {
  // 有反馈时会重新走 TL 分析，不落确认卡片
  if (feedback) return
  // 直接确认：将 plan_confirmed 卡片插入到 TL plan 消息之前（后端已持久化同样一条）
  const spec = specData.value
  const card: any = {
    role: 'user',
    name: '用户',
    content: '已确认开发计划，开始编码',
    plan_confirmed: {
      features: spec?.features || [],
      tech_stack: spec?.tech_stack || {},
      file_structure: spec?.file_structure || [],
      complexity: spec?.complexity || 'S',
    },
  }
  // 确认卡片应插入到 TL 分析消息之前（spec 事件到达时记录的索引，
  // 避免搜索 plan 字段——SSE dialogue 事件不携带结构化字段）
  const insertAt = store._specInsertIndex ?? store.dialogueMessages.length
  store.dialogueMessages.splice(insertAt, 0, card)
}

async function onQuestionSubmitted(answers?: Record<string, string>) {
  if (store.pendingChatClarification && answers) {
    // Chat 模式澄清：拼接答案后重新发送
    if (answers._skip) {
      // 用户跳过，用原始消息继续
      const { originalMessage } = store.pendingChatClarification
      store.pendingChatClarification = null
      store.questionForm = null
      emit('send-message', originalMessage)
    } else {
      // 拼接答案到原始消息（LLM 上下文用完整消息，展示用已提交卡片）
      const questions = store.questionForm?.questions || []
      const answerText = Object.entries(answers)
        .filter(([, v]) => v)
        .map(([q, a]) => `${q}: ${a}`)
        .join('；')
      const { originalMessage } = store.pendingChatClarification
      const enrichedMessage = `[用户补充说明]\n${answerText}\n\n原始修改意见：${originalMessage}`

      store.pendingChatClarification = null
      store.questionForm = null

      // 已完成表单作为特殊 user 消息进入消息流（后端持久化同样一条）
      store.addDialogueMessage({
        role: 'user',
        name: '用户',
        content: answerText || '已确认',
        question_form: { questions, submitted: true, answers: { ...answers } },
      })

      emit('send-message', enrichedMessage, { questions, answers: { ...answers } })
    }
  } else {
    // 新需求 SOP 模式：QuestionForm 内部已调用 /clarify 并把已完成表单落成消息流卡片
  }
}

// Auto-scroll to bottom when new messages arrive
watch(
  () => store.dialogueMessages.length,
  () => {
    nextTick(() => {
      if (bodyRef.value) {
        bodyRef.value.scrollTop = bodyRef.value.scrollHeight
      }
    })
  }
)
</script>

<style scoped>
.dialogue-panel {
  flex: 0 0 40%;
  display: flex;
  flex-direction: column;
  background: var(--surface);
  min-width: 0;
}

.dialogue-header {
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
  flex-shrink: 0;
}

.dialogue-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dialogue-body::-webkit-scrollbar {
  width: 6px;
}

.dialogue-body::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 3px;
}

.loading-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 12px;
  align-self: center;
  margin-top: auto;
}

.lb-text {
  font-size: 13px;
  color: var(--muted);
}

.lb-dots {
  display: flex;
  gap: 4px;
}

.lb-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: lb-bounce 0.6s infinite alternate;
}

.lb-dot:nth-child(2) {
  animation-delay: 0.2s;
}

.lb-dot:nth-child(3) {
  animation-delay: 0.4s;
}
</style>
