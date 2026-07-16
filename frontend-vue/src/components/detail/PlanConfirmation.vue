<template>
  <div class="plan-card">
    <div class="plan-card-header">
      <span class="plan-card-icon">🎯</span>
      <span class="plan-card-title">开发计划确认</span>
      <span class="plan-card-badge" :class="complexityClass">{{ specData?.complexity || 'S' }}</span>
    </div>

    <div class="plan-card-summary">
      <!-- 功能列表 -->
      <div v-if="specData?.features?.length" class="plan-features">
        <div class="plan-label">核心功能</div>
        <div class="plan-tags">
          <span v-for="(f, i) in specData.features" :key="i" class="plan-tag feature-tag">{{ f }}</span>
        </div>
      </div>

      <!-- 技术栈 -->
      <div v-if="techStackText" class="plan-tech">
        <div class="plan-label">技术栈</div>
        <span class="plan-tag tech-tag">{{ techStackText }}</span>
      </div>

      <!-- 文件结构 -->
      <div v-if="specData?.file_structure?.length" class="plan-files">
        <div class="plan-label">文件结构 ({{ specData.file_structure.length }} 个文件)</div>
        <div class="plan-file-list">
          <span v-for="(f, i) in specData.file_structure.slice(0, 6)" :key="i" class="plan-file-item">📄 {{ f }}</span>
          <span v-if="specData.file_structure.length > 6" class="plan-file-item more">
            …还有 {{ specData.file_structure.length - 6 }} 个文件
          </span>
        </div>
      </div>

      <!-- 数据模型 -->
      <div v-if="specData?.data_model" class="plan-data-model">
        <div class="plan-label">数据模型</div>
        <div class="plan-desc">{{ specData.data_model }}</div>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div class="plan-actions">
      <button class="btn-modify" @click="showFeedback = !showFeedback">
        ✏️ {{ showFeedback ? '收起' : '修改需求' }}
      </button>
      <button class="btn-confirm" @click="onConfirm" :disabled="confirming">
        {{ confirming ? '确认中…' : '✅ 确认，开始编码' }}
      </button>
    </div>

    <!-- 反馈输入框 -->
    <div v-if="showFeedback" class="plan-feedback">
      <textarea
        v-model="feedbackText"
        placeholder="输入你的修改意见，例如：请使用 React 而不是原生 JS、增加暗黑模式切换功能…"
        rows="3"
        class="feedback-input"
      ></textarea>
      <button
        class="btn-confirm-with-feedback"
        @click="onConfirmWithFeedback"
        :disabled="!feedbackText.trim() || confirming"
      >
        确认修改，重新分析
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { SSESpecData } from '@/types/sse'
import { useRequirementStore } from '@/stores/requirement'

const props = defineProps<{
  specData: SSESpecData | null
}>()

const emit = defineEmits<{
  (e: 'confirmed', feedback: string): void
}>()

const store = useRequirementStore()
const showFeedback = ref(false)
const feedbackText = ref('')
const confirming = ref(false)

const complexityClass = computed(() => {
  const c = props.specData?.complexity || 'S'
  return `complexity-${c.toLowerCase()}`
})

const techStackText = computed(() => {
  const ts = props.specData?.tech_stack
  if (!ts) return ''
  const parts: string[] = []
  if (ts.framework) parts.push(`框架: ${ts.framework}`)
  if (ts.css) parts.push(`CSS: ${ts.css}`)
  if (ts.storage) parts.push(`存储: ${ts.storage}`)
  return parts.join(' · ')
})

async function onConfirm() {
  confirming.value = true
  try {
    // 不带反馈，直接确认
    await store.confirmPlan('')
    emit('confirmed', '')
  } finally {
    confirming.value = false
  }
}

async function onConfirmWithFeedback() {
  if (!feedbackText.value.trim()) return
  confirming.value = true
  try {
    // 带反馈确认：后端会重新走 TL 节点
    await store.confirmPlan(feedbackText.value.trim())
    emit('confirmed', feedbackText.value.trim())
  } finally {
    confirming.value = false
  }
}
</script>

<style scoped>
.plan-card {
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: 12px;
  padding: 16px;
  margin: 0;
  align-self: stretch;
}

.plan-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.plan-card-icon {
  font-size: 18px;
}

.plan-card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
}

.plan-card-badge {
  margin-left: auto;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  color: #fff;
}

.complexity-xs { background: oklch(55% 0.1 155); }
.complexity-s { background: oklch(55% 0.1 155); }
.complexity-m { background: oklch(65% 0.12 85); }
.complexity-l { background: oklch(50% 0.2 25); }

.plan-card-summary {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.plan-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-bottom: 2px;
}

.plan-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.plan-tag {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
  font-weight: 500;
}

.feature-tag {
  background: oklch(97% 0.01 250 / 0.5);
  color: oklch(50% 0.1 250);
  border: 1px solid oklch(85% 0.02 250);
}

.tech-tag {
  background: oklch(97% 0.01 80 / 0.5);
  color: oklch(55% 0.1 80);
  border: 1px solid oklch(85% 0.04 80);
}

.plan-file-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.plan-file-item {
  font-size: 11px;
  font-family: var(--font-mono, monospace);
  color: var(--muted);
  background: var(--bg);
  padding: 1px 6px;
  border-radius: 4px;
}

.plan-file-item.more {
  color: var(--accent);
  font-style: italic;
}

.plan-desc {
  font-size: 12px;
  color: var(--fg);
  line-height: 1.5;
  padding: 6px 10px;
  background: var(--bg);
  border-radius: 6px;
}

.plan-actions {
  display: flex;
  gap: 8px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
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

.btn-modify:hover {
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

.btn-confirm:disabled {
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

.btn-confirm-with-feedback:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
