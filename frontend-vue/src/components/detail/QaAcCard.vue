<template>
  <div class="qa-card" :class="'qa-card-' + statusClass">
    <!-- 折叠栏：AC 编号 + 结论 + 步数 + 起止时间 -->
    <div class="qa-card-toggle" @click="expanded = !expanded">
      <span class="qa-card-icon">{{ expanded ? '▾' : '▸' }}</span>
      <span class="qa-card-name">
        <span class="qa-card-role-icon">🔍</span>
        {{ msg.name || 'Catherine（质量工程师）' }}
      </span>
      <span class="qa-card-ac">[{{ acId }}]</span>
      <span class="qa-card-label">{{ label }}</span>

      <span v-if="isLive" class="qa-card-live">
        <span class="qa-card-live-dot"></span>验收中…
      </span>
      <span v-else class="qa-card-badge" :class="'badge-' + statusClass">
        {{ statusText }}
      </span>

      <span class="qa-card-steps">{{ steps.length }} 步</span>
      <span v-if="timeRange" class="qa-card-time">{{ timeRange }}</span>
    </div>

    <!-- 结论摘要（失败/不适用时直接可见，不必展开） -->
    <div v-if="!isLive && summary" class="qa-card-summary">{{ summary }}</div>

    <!-- 展开：逐步操作时间线 -->
    <div v-if="expanded" class="qa-card-body">
      <div
        v-for="(s, i) in steps"
        :key="i"
        class="qa-step-row"
        :class="'qa-' + (s.status || 'ok')"
      >
        <span class="qa-step-idx">{{ i + 1 }}</span>
        <span class="qa-step-icon">{{ stepIcon(s.status) }}</span>
        <span class="qa-step-action">{{ stepLabel(s) }}</span>
        <span v-if="s.selector" class="qa-step-selector">{{ s.selector }}</span>
        <span v-if="s.value" class="qa-step-value">= {{ s.value }}</span>
        <span v-if="s.detail" class="qa-step-detail">{{ s.detail }}</span>
        <span v-if="s.timestamp" class="qa-step-time">{{ shortTime(s.timestamp) }}</span>
      </div>
      <div v-if="!steps.length" class="qa-card-empty">暂无步骤记录</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { DialogueMessage } from '@/types/api'

const props = defineProps<{ msg: DialogueMessage }>()

const expanded = ref(false)

const ac = computed(() => (props.msg as any).qa_result || {})
const acId = computed(() => ac.value.ac_id || '')
const label = computed(() => ac.value.label || '')
const steps = computed<any[]>(() => ac.value.steps || [])
const summary = computed(() => ac.value.summary || '')
const isLive = computed(() => (props.msg as any).live === true)

const statusClass = computed(() => {
  if (isLive.value) return 'running'
  return String(ac.value.status || 'ok')
})

const statusText = computed(() => {
  switch (statusClass.value) {
    case 'running': return '验收中'
    case 'ok': return '通过'
    case 'fail': return '未通过'
    case 'error': return '执行异常'
    case 'na': return '不适用'
    default: return statusClass.value
  }
})

// 验收进行中时自动展开，让每一步实时可见；结束后收起，避免刷屏
watch(isLive, (v) => { if (v) expanded.value = true })

function shortTime(ts?: string | null): string {
  if (!ts) return ''
  // 后端时间戳形如 "2026-09-26 21:53:06"，只取时间部分
  const m = String(ts).match(/(\d{2}:\d{2}:\d{2})/)
  return m ? m[1] : String(ts)
}

const timeRange = computed(() => {
  const s = shortTime(ac.value.start_ts)
  const e = shortTime(ac.value.end_ts)
  if (s && e) return `${s} – ${e}`
  return s || e || shortTime(props.msg.timestamp)
})

const ACTION_LABELS: Record<string, string> = {
  click: '点击', type: '输入', select: '选择', press: '按键', wait: '等待',
  screenshot: '截图', assert_exists: '断言存在', assert_visible: '断言可见',
  assert_text: '断言文本', assert_count: '断言数量', assert_value: '断言值',
  assert_canvas_change: '断言画面变化', assert_dom_change: '断言界面更新',
}

function stepLabel(s: any): string {
  const a = s?.action || ''
  return `${ACTION_LABELS[a] || a}`
}

function stepIcon(status?: string): string {
  switch (status) {
    case 'fail': return '❌'
    case 'error': return '⚠️'
    case 'na': return '➖'
    default: return '✅'
  }
}
</script>

<style scoped>
.qa-card {
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px;
  background: var(--surface, #fff);
  overflow: hidden;
}
.qa-card-fail { border-left: 3px solid #ef4444; }
.qa-card-error { border-left: 3px solid #f59e0b; }
.qa-card-na { border-left: 3px solid #9ca3af; }
.qa-card-ok { border-left: 3px solid #22c55e; }
.qa-card-running { border-left: 3px solid #3b82f6; }

.qa-card-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  cursor: pointer;
  font-size: 13px;
  user-select: none;
}
.qa-card-toggle:hover { background: var(--hover, #f9fafb); }
.qa-card-icon { color: var(--muted, #6b7280); width: 12px; }
.qa-card-name { font-weight: 600; display: flex; align-items: center; gap: 4px; }
.qa-card-role-icon { font-size: 12px; }
.qa-card-ac { color: var(--muted, #6b7280); font-variant-numeric: tabular-nums; }
.qa-card-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted, #6b7280);
}
.qa-card-steps { font-size: 12px; color: var(--muted, #6b7280); }
.qa-card-time {
  font-size: 12px;
  color: var(--muted, #6b7280);
  font-variant-numeric: tabular-nums;
}
.qa-card-live {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #3b82f6;
}
.qa-card-live-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: #3b82f6;
  animation: qa-pulse 1.2s ease-in-out infinite;
}
@keyframes qa-pulse { 0%,100% { opacity: 1 } 50% { opacity: .3 } }

.qa-card-badge {
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 10px;
  white-space: nowrap;
}
.badge-ok { background: #dcfce7; color: #15803d; }
.badge-fail { background: #fee2e2; color: #b91c1c; }
.badge-error { background: #fef3c7; color: #b45309; }
.badge-na { background: #f3f4f6; color: #4b5563; }
.badge-running { background: #dbeafe; color: #1d4ed8; }

.qa-card-summary {
  padding: 0 10px 8px 30px;
  font-size: 12px;
  color: #b91c1c;
  line-height: 1.5;
}

.qa-card-body {
  border-top: 1px dashed var(--border, #e5e7eb);
  padding: 6px 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.qa-card-empty { font-size: 12px; color: var(--muted, #6b7280); padding: 4px 0; }

.qa-step-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  padding: 2px 0;
  line-height: 1.5;
}
.qa-step-idx {
  color: var(--muted, #9ca3af);
  min-width: 18px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.qa-step-action { font-weight: 500; }
.qa-step-selector {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: var(--muted, #6b7280);
}
.qa-step-value { color: var(--muted, #6b7280); font-size: 11px; }
.qa-step-detail { color: var(--muted, #6b7280); font-size: 11px; }
.qa-step-time {
  margin-left: auto;
  font-size: 11px;
  color: #9ca3af;
  font-variant-numeric: tabular-nums;
}
.qa-fail .qa-step-action, .qa-fail .qa-step-detail { color: #b91c1c; }
.qa-error .qa-step-action, .qa-error .qa-step-detail { color: #b45309; }
</style>
