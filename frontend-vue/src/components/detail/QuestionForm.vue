<template>
  <!-- 可编辑表单（提交后转为消息流中的"已提交"卡片，由 DialogueMessage 渲染） -->
  <div v-if="formData" class="question-form-card">
    <div class="qf-title">
      {{ mode === 'chat' ? '补充信息，让修改更精准' : '补充信息，让 AI 更好地理解你的需求' }}
    </div>
    <div v-for="q in formData.questions" :key="q.id" class="qf-item">
      <div class="qf-label">{{ q.label }}</div>
      <div v-if="q.type === 'radio' && q.options" class="qf-options">
        <span
          v-for="(opt, i) in q.options"
          :key="i"
          :class="['qf-radio', { selected: answers[q.id] === opt }]"
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
    <div class="qf-actions">
      <button class="qf-skip" :disabled="submitting" @click="skipForm">
        {{ submitting ? '处理中…' : '跳过，使用默认方案' }}
      </button>
      <button class="qf-submit" :disabled="submitting" @click="submitForm">
        {{ submitting ? '提交中…' : '提交补充信息' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRequirementStore } from '@/stores/requirement'
import { useToast } from '@/composables/useToast'
import type { SSEQuestionFormData } from '@/types/sse'

const props = defineProps<{
  formData?: SSEQuestionFormData | null
  mode?: 'requirement' | 'chat'
}>()

const emit = defineEmits<{
  submitted: [answers?: Record<string, string>]
}>()

const store = useRequirementStore()
const { show } = useToast()
const answers = reactive<Record<string, string>>({})
const submitting = ref(false)

function selectRadio(qid: string, value: string) {
  answers[qid] = value
}

/** 提交/跳过后：清除浮动表单，把已完成的表单落成消息流中的特殊 user 消息（后端持久化同样一条） */
function finalizeAsMessage(content: string, submittedAnswers: Record<string, string>) {
  if (!props.formData) return
  store.addDialogueMessage({
    role: 'user',
    name: '用户',
    content,
    question_form: {
      questions: props.formData.questions,
      submitted: true,
      answers: submittedAnswers,
    },
  })
  store.questionForm = null
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
    if (props.mode === 'chat') {
      // Chat 模式：将答案传给父组件处理，不清除 questionForm
      // （父组件负责拼接消息并重新发送 chat 请求）
      emit('submitted', { ...answers })
      return  // 不清除 submitting 状态，由父组件控制
    }

    // 新需求 SOP 模式：调用 /clarify 接口
    store.isGenerating = true  // 立即显示加载态，等待 SSE 推送进度
    await store.submitClarification({ ...answers })
    const answerText = Object.entries(answers)
      .map(([q, a]) => `${q}: ${a}`)
      .join('；')
    finalizeAsMessage(answerText, { ...answers })
    emit('submitted')
  } catch (err: any) {
    show(err.message || '提交失败', 'error')
  } finally {
    if (props.mode !== 'chat') {
      submitting.value = false
    }
  }
}

async function skipForm() {
  if (!props.formData) return

  submitting.value = true
  try {
    if (props.mode === 'chat') {
      // Chat 模式跳过：使用原始消息继续
      emit('submitted', { _skip: 'true' })
      return
    }

    // 新需求 SOP 模式
    await store.submitClarification({ _skip: 'true' })
    finalizeAsMessage(
      '_skip: true',
      Object.fromEntries(
        props.formData.questions.map((q) => [q.id, '（使用默认方案）'])
      )
    )
    emit('submitted')
    show('已跳过，使用默认方案重新处理…', 'success')
  } catch (err: any) {
    show(err.message || '提交失败', 'error')
  } finally {
    if (props.mode !== 'chat') {
      submitting.value = false
    }
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

.qf-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 10px;
}

.qf-skip {
  padding: 8px 16px;
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 13px;
  font-family: var(--font-body);
  color: var(--muted);
  background: transparent;
  cursor: pointer;
  transition: all 0.15s;
}

.qf-skip:hover:not(:disabled) {
  color: var(--fg);
  border-color: var(--muted);
}

.qf-skip:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
}

.qf-submit:hover:not(:disabled) {
  filter: brightness(1.1);
}

.qf-submit:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
