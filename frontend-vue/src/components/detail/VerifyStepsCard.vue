<template>
  <div class="vs-card" :class="'vs-card-' + statusClass">
    <!-- 折叠栏：角色 + 轮次 + 结论 + 时间 -->
    <div class="vs-toggle" @click="expanded = !expanded">
      <span class="vs-caret">{{ expanded ? '▾' : '▸' }}</span>
      <span class="vs-name">
        <span class="vs-role-icon">🔍</span>
        {{ msg.name || 'Catherine（质量工程师）' }}
      </span>
      <span v-if="round > 0" class="vs-round">第 {{ round + 1 }} 轮</span>
      <span v-if="isLive" class="vs-live">
        <span class="vs-live-dot"></span>{{ liveText }}
      </span>
      <span v-else class="vs-badge" :class="'badge-' + statusClass">{{ statusText }}</span>
      <span class="vs-steps">{{ doneCount }}/{{ steps.length }} 步</span>
      <span v-if="timeRange" class="vs-time">{{ timeRange }}</span>
    </div>

    <!-- 结论摘要：结束态直接可见，不必展开 -->
    <div v-if="!isLive && summaryLine" class="vs-summary">{{ summaryLine }}</div>

    <!-- 展开：子步骤时间线 -->
    <div v-if="expanded" class="vs-body">
      <div v-for="(s, i) in steps" :key="s.key || i" class="vs-row" :class="'vs-' + (s.status || 'pending')">
        <span class="vs-idx">{{ i + 1 }}</span>
        <span class="vs-icon">{{ stepIcon(s.status) }}</span>
        <span class="vs-label">{{ s.label || s.key }}</span>
        <span v-if="s.detail" class="vs-detail">{{ s.detail }}</span>
      </div>
      <div v-if="!steps.length" class="vs-empty">暂无步骤记录</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { DialogueMessage, VerifyCard, VerifyStep } from '@/types/api'

const props = defineProps<{ msg: DialogueMessage }>()

const expanded = ref(false)

// 实时卡（role=verify_steps）与落库卡（role=qa_summary）共用同一份形状，
// 差异只在承载字段不同 —— 所以这里只按"取得到哪一份"取值。
const card = computed<VerifyCard>(() => {
  const m = props.msg as unknown as { qa_summary?: VerifyCard; verify_steps?: VerifyCard }
  return m.qa_summary || m.verify_steps || { steps: [] }
})
const steps = computed<VerifyStep[]>(() => card.value.steps || [])
const round = computed(() => Number(card.value.round ?? 0))
const isLive = computed(() => (props.msg as any).live === true)

const doneCount = computed(
  () => steps.value.filter((s) => s.status === 'done' || s.status === 'failed').length,
)

const statusClass = computed(() => {
  if (isLive.value) return 'running'
  const v = String(card.value.verdict || '').toUpperCase()
  if (v === 'PASS') return 'ok'
  if (v === 'NEEDS_WORK') return 'fail'
  return 'unknown'
})

const statusText = computed(() => {
  switch (statusClass.value) {
    case 'ok': return '通过'
    case 'fail': return '待修复'
    default: return card.value.verdict || '已完成'
  }
})

// 实时态取当前正在跑/最后完成的步骤名，避免卡片头只写"进行中"
const liveText = computed(() => {
  const running = steps.value.find((s) => s.status === 'running')
  if (running) return running.label
  const last = [...steps.value].reverse().find((s) => s.status && s.status !== 'pending')
  return last?.label || '验证中…'
})

// 结束态摘要：verdict + 分数 + 验收比例 + 缺陷数，一行看清结论
const summaryLine = computed(() => {
  if (isLive.value) return ''
  const parts: string[] = []
  if (card.value.score != null) parts.push(`评分 ${card.value.score}/10`)
  const ac = card.value.ac
  if (ac && ac.total) parts.push(`验收 ${ac.passed}/${ac.total} 通过`)
  if (card.value.defect_count) parts.push(`${card.value.defect_count} 条待修`)
  if (card.value.browser_errors) parts.push(`${card.value.browser_errors} 个运行时错误`)
  if (card.value.fast_pass) parts.push('快速通道（未做深度评估）')
  return parts.join(' · ')
})

// 实时展开让每一步可见；结束后收起，避免长卡刷屏。
// 必须 immediate —— 实时卡被创建时 live 已经是 true，没有「变化」可触发，
// 不加 immediate 的话每次都是折叠态，用户照样看不到后台在做什么。
watch(isLive, (v) => { expanded.value = v }, { immediate: true })

function shortTime(ts?: string | null): string {
  if (!ts) return ''
  const m = String(ts).match(/(\d{2}:\d{2}:\d{2})/)
  return m ? m[1] : String(ts)
}

const timeRange = computed(() => {
  const s = shortTime(card.value.start_ts)
  const e = shortTime(card.value.end_ts)
  if (s && e) return `${s} – ${e}`
  return s || e || shortTime(props.msg.timestamp)
})

function stepIcon(status?: string): string {
  switch (status) {
    case 'done': return '✅'
    case 'failed': return '❌'
    case 'skipped': return '➖'
    case 'running': return '⏳'
    default: return '○'
  }
}
</script>

<style scoped>
.vs-card {
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px;
  background: var(--surface, #fff);
  overflow: hidden;
}
.vs-card-ok { border-left: 3px solid #22c55e; }
.vs-card-fail { border-left: 3px solid #ef4444; }
.vs-card-running { border-left: 3px solid #3b82f6; }
.vs-card-unknown { border-left: 3px solid #9ca3af; }

.vs-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  cursor: pointer;
  font-size: 13px;
  user-select: none;
  /* 详情页右侧预览占位后对话栏很窄，一行塞不下「角色+轮次+徽章+步数+时间」。
     允许换行，并让每个 token 整体不折断 —— 否则「第 3 轮」会被拆成「第 3」/「轮」。 */
  flex-wrap: wrap;
  row-gap: 3px;
}
.vs-toggle:hover { background: var(--hover, #f9fafb); }
.vs-caret { color: var(--muted, #6b7280); width: 12px; flex: 0 0 auto; }
.vs-name {
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.vs-role-icon { font-size: 12px; flex: 0 0 auto; }
.vs-round {
  font-size: 12px;
  color: var(--muted, #6b7280);
  background: var(--hover, #f3f4f6);
  border-radius: 8px;
  padding: 0 6px;
  flex: 0 0 auto;
  white-space: nowrap;
}
.vs-steps {
  margin-left: auto;
  font-size: 12px;
  color: var(--muted, #6b7280);
  white-space: nowrap;
  flex: 0 0 auto;
}
.vs-time {
  font-size: 12px;
  color: var(--muted, #6b7280);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  flex: 0 0 auto;
}
.vs-live {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #3b82f6;
  white-space: nowrap;
  flex: 0 0 auto;
}
.vs-live-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: #3b82f6;
  animation: vs-pulse 1.2s ease-in-out infinite;
}
@keyframes vs-pulse { 0%,100% { opacity: 1 } 50% { opacity: .3 } }

.vs-badge { font-size: 12px; padding: 1px 6px; border-radius: 10px; white-space: nowrap; }
.badge-ok { background: #dcfce7; color: #15803d; }
.badge-fail { background: #fee2e2; color: #b91c1c; }
.badge-unknown { background: #f3f4f6; color: #4b5563; }

.vs-summary {
  padding: 0 10px 8px 30px;
  font-size: 12px;
  color: var(--muted, #6b7280);
  line-height: 1.5;
}

.vs-body {
  border-top: 1px dashed var(--border, #e5e7eb);
  padding: 6px 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.vs-empty { font-size: 12px; color: var(--muted, #6b7280); padding: 4px 0; }

.vs-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  padding: 2px 0;
  line-height: 1.5;
}
.vs-idx {
  color: var(--muted, #9ca3af);
  min-width: 18px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.vs-label { font-weight: 500; }
.vs-detail { color: var(--muted, #6b7280); font-size: 11px; }
.vs-pending .vs-label { color: var(--muted, #6b7280); font-weight: 400; }
.vs-skipped .vs-label, .vs-skipped .vs-detail { color: #9ca3af; }
.vs-failed .vs-label, .vs-failed .vs-detail { color: #b91c1c; }
.vs-running .vs-label { color: #1d4ed8; }
</style>
