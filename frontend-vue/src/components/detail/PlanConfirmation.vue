<template>
  <PlanSummaryCard :spec="specData">
    <div class="plan-actions">
      <button
        class="btn-modify"
        @click="showFeedback = !showFeedback"
        :disabled="locking"
      >
        ✏️ {{ showFeedback ? '收起' : '修改需求' }}
      </button>
      <button class="btn-confirm" @click="onConfirm" :disabled="confirming || locking">
        {{ locking ? '重新分析中…' : confirming ? '确认中…' : '✅ 确认，开始编码' }}
      </button>
    </div>

    <!-- 反馈输入框 -->
    <div v-if="showFeedback" class="plan-feedback">
      <textarea
        v-model="feedbackText"
        placeholder="输入你的修改意见，例如：请使用 React 而不是原生 JS、增加暗黑模式切换功能…"
        rows="3"
        class="feedback-input"
        :disabled="locking"
      ></textarea>
      <button
        class="btn-confirm-with-feedback"
        @click="onConfirmWithFeedback"
        :disabled="!feedbackText.trim() || confirming || locking"
      >
        {{ locking ? '已提交修改意见，正在重新分析…' : '确认修改，重新分析' }}
      </button>
    </div>
  </PlanSummaryCard>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { SSESpecData } from '@/types/sse'
import { useRequirementStore } from '@/stores/requirement'
import PlanSummaryCard from './PlanSummaryCard.vue'

const props = defineProps<{
  specData: SSESpecData | null
}>()

const store = useRequirementStore()
const showFeedback = ref(false)
const feedbackText = ref('')
const confirming = ref(false)
// 带反馈重分析期间：后端已把需求置回 pending/processing，此时再点「确认」会被拒（400）。
// 不锁住的话这张卡还挂在界面上、按钮还能按，用户只会收获一个静默失败。
const locking = ref(false)

// 新的 spec 到达（对象被整体替换）说明重分析有结果了，解锁让用户重新确认
watch(
  () => props.specData,
  () => {
    locking.value = false
  }
)

async function onConfirm() {
  confirming.value = true
  try {
    // 不带反馈，直接确认。卡片由 DialoguePanel watch planStatus 落成消息
    // —— 本组件在确认成功的同一帧就被卸载，emit 会被 Vue 丢弃。
    await store.confirmPlan('')
  } finally {
    confirming.value = false
  }
}

async function onConfirmWithFeedback() {
  if (!feedbackText.value.trim()) return
  confirming.value = true
  try {
    // 带反馈确认：后端会重新走 TL 节点，不落确认卡片，因此本卡片留在原地等新计划
    await store.confirmPlan(feedbackText.value.trim())
    locking.value = true
  } finally {
    confirming.value = false
  }
}
</script>

<style scoped>
.plan-actions {
  display: flex;
  gap: 8px;
}

.btn-modify,
.btn-confirm,
.btn-confirm-with-feedback {
  font-size: 13px;
  font-weight: 600;
  font-family: var(--font-body);
  padding: 8px 16px;
  border-radius: 8px;
  cursor: pointer;
  border: none;
  transition: background 0.15s, opacity 0.15s;
}

.btn-modify {
  background: var(--bg);
  color: var(--fg);
  border: 1px solid var(--border);
}

.btn-modify:hover:not(:disabled) {
  background: var(--border);
}

.btn-confirm {
  flex: 1;
  background: var(--accent);
  color: #fff;
}

.btn-confirm:hover:not(:disabled) {
  background: oklch(58% 0.13 28);
}

.btn-modify:disabled,
.btn-confirm:disabled,
.btn-confirm-with-feedback:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.plan-feedback {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.feedback-input {
  width: 100%;
  font-family: var(--font-body);
  font-size: 13px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
  color: var(--fg);
  resize: vertical;
  min-height: 60px;
  box-sizing: border-box;
}

.feedback-input:focus {
  outline: none;
  border-color: var(--accent);
}

.feedback-input::placeholder {
  color: var(--muted);
}

.btn-confirm-with-feedback {
  background: oklch(58% 0.13 28);
  color: #fff;
}

.btn-confirm-with-feedback:hover:not(:disabled) {
  background: oklch(50% 0.14 28);
}
</style>
