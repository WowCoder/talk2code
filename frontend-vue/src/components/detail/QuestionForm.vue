<template>
  <div v-if="formData" class="question-form-card">
    <div class="qf-title">补充信息，让 AI 更好地理解你的需求</div>
    <div v-for="q in formData.questions" :key="q.id" class="qf-item">
      <div class="qf-label">{{ q.label }}</div>
      <div v-if="q.type === 'radio' && q.options" class="qf-options">
        <span
          v-for="(opt, i) in q.options"
          :key="i"
          :class="['qf-radio', { selected: answers[q.id] === opt || (!answers[q.id] && i === 0) }]"
          @click="selectRadio(q.id, opt)"
        >
          {{ opt }}
        </span>
      </div>
      <input
        v-else
        v-model="answers[q.id]"
        type="text"
        class="qf-input"
        placeholder="请输入…"
      />
    </div>
    <button class="qf-submit" :disabled="submitting" @click="submitForm">
      {{ submitting ? '提交中…' : '提交补充信息' }}
    </button>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRequirementStore } from '@/stores/requirement'
import { useToast } from '@/composables/useToast'
import type { SSEQuestionFormData } from '@/types/sse'

const props = defineProps<{
  formData?: SSEQuestionFormData | null
}>()

const emit = defineEmits<{
  submitted: []
}>()

const store = useRequirementStore()
const { show } = useToast()
const answers = reactive<Record<string, string>>({})
const submitting = ref(false)

function selectRadio(qid: string, value: string) {
  answers[qid] = value
}

async function submitForm() {
  if (!props.formData) return
  // Collect answers (default first option for unanswered radio questions)
  props.formData.questions.forEach((q) => {
    if (!answers[q.id] && q.type === 'radio' && q.options?.length) {
      answers[q.id] = q.options[0]
    }
  })

  submitting.value = true
  try {
    await store.submitClarification({ ...answers })
    emit('submitted')
  } catch (err: any) {
    show(err.message || '提交失败', 'error')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.question-form-card {
  background: var(--accent-soft);
  border-radius: 12px;
  padding: 16px;
  margin: 8px 0;
  align-self: flex-start;
  width: 100%;
}

.qf-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 12px;
}

.qf-item {
  margin-bottom: 10px;
}

.qf-label {
  font-size: 12px;
  color: var(--fg);
  margin-bottom: 4px;
}

.qf-options {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.qf-radio {
  border: 1px solid var(--border);
  padding: 5px 12px;
  border-radius: 100px;
  font-size: 12px;
  cursor: pointer;
  background: var(--surface);
  color: var(--muted);
  transition: all 0.15s;
  user-select: none;
}

.qf-radio.selected {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

.qf-input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  font-family: var(--font-body);
  color: var(--fg);
  background: var(--bg);
  outline: none;
}

.qf-input:focus {
  border-color: var(--accent);
}

.qf-submit {
  padding: 8px 20px;
  border: none;
  border-radius: 10px;
  background: var(--accent);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  font-family: var(--font-body);
  cursor: pointer;
  margin-top: 8px;
}

.qf-submit:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
