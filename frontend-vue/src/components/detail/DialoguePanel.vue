<template>
  <div class="dialogue-panel">
    <div class="dialogue-header">AI 对话</div>
    <div class="dialogue-body" ref="bodyRef">
      <template v-for="(msg, i) in messages" :key="i">
        <DialogueMessage :msg="msg" />
      </template>

      <!-- Dynamic components from SSE -->
      <QuestionForm
        v-if="questionForm"
        :form-data="questionForm"
        :mode="questionFormMode"
        @submitted="onQuestionSubmitted"
      />
      <PermissionRequest
        v-if="permissionRequest"
        :request="permissionRequest"
        @resolved="onPermissionResolved"
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
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { useRequirementStore } from '@/stores/requirement'
import DialogueMessage from './DialogueMessage.vue'
import DialogueInput from './DialogueInput.vue'
import QuestionForm from './QuestionForm.vue'
import PermissionRequest from './PermissionRequest.vue'
import ExecutionPanel from './ExecutionPanel.vue'
import type { DialogueMessage as DialogueMessageType } from '@/types/api'
import type { SSEQuestionFormData, SSEPermissionData, SSETraceSummaryData } from '@/types/sse'

const emit = defineEmits<{
  (e: 'send-message', message: string): void
}>()

const bodyRef = ref<HTMLElement | null>(null)
const store = useRequirementStore()

const messages = computed(() => store.dialogueMessages.filter(
  (m: DialogueMessageType) => m.content !== '__QUESTION_FORM__'
))
const isLoading = computed(() => store.isGenerating)

// Access SSE-triggered state from the store
const questionForm = computed(() => store.questionForm)
const questionFormMode = computed(() =>
  store.pendingChatClarification ? 'chat' : 'requirement'
)
const permissionRequest = computed(() => (store as any)._permissionRequest as (SSEPermissionData & { timestamp: number }) | null)
const traceSummary = computed(() => (store as any)._traceSummary as SSETraceSummaryData | null)

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
      // 拼接答案到原始消息
      const answerText = Object.entries(answers)
        .filter(([, v]) => v)
        .map(([q, a]) => `${q}: ${a}`)
        .join('；')
      const { originalMessage } = store.pendingChatClarification
      const enrichedMessage = `[用户补充说明]\n${answerText}\n\n原始修改意见：${originalMessage}`

      store.pendingChatClarification = null
      store.questionForm = null

      // 将答案作为对话消息展示
      store.addDialogueMessage({
        role: 'user',
        name: '用户',
        content: answerText || '已确认',
      })

      emit('send-message', enrichedMessage)
    }
  } else {
    // 新需求 SOP 模式：QuestionForm 内部已调用 /clarify + 保留为已提交卡片
    // 不做任何清除，让 QuestionForm 展示 submitted 状态
  }
}

function onPermissionResolved() {
  (store as any)._permissionRequest = null
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
