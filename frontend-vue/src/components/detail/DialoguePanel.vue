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
      @send="$emit('send-message', $event)"
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

const bodyRef = ref<HTMLElement | null>(null)
const store = useRequirementStore()

const messages = computed(() => store.dialogueMessages.filter(
  (m: DialogueMessageType) => m.content !== '__QUESTION_FORM__'
))
const isLoading = computed(() => store.isGenerating)

// Access SSE-triggered state from the store
const questionForm = computed(() => (store as any)._questionForm as SSEQuestionFormData | null)
const permissionRequest = computed(() => (store as any)._permissionRequest as (SSEPermissionData & { timestamp: number }) | null)
const traceSummary = computed(() => (store as any)._traceSummary as SSETraceSummaryData | null)

function onQuestionSubmitted() {
  (store as any)._questionForm = null
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
