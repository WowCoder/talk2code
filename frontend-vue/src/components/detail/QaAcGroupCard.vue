<template>
  <div class="qa-group-card" :class="'qa-group-' + statusClass">
    <!-- 折叠栏：AC 编号 + 最终结论 + 验收次数。
         名字与时间各自占了太多横向空间（会话面板只有 40% 宽），挤在一行会把
         标签折行、时间截断 —— 所以拆成两行：主行给「谁 + 哪条 + 结果」，
         次行给「第几次/趋势/时间」这类弱信息。 -->
    <div class="qg-toggle" @click="expanded = !expanded">
      <div class="qg-main">
        <span class="qg-icon">{{ expanded ? '▾' : '▸' }}</span>
        <span class="qg-name" :title="msg.name || 'Catherine（质量工程师）'">
          <span class="qg-role-icon">🔍</span>{{ shortName }}
        </span>
        <span class="qg-ac">[{{ acId }}]</span>
        <span class="qg-label" :title="label">{{ label }}</span>
        <span v-if="isLive" class="qg-live">
          <span class="qg-live-dot"></span>验收中…
        </span>
        <span v-else class="qg-badge" :class="'badge-' + statusClass">
          {{ statusText }} · {{ roundMsgs.length }} 次
        </span>
      </div>
      <div class="qg-meta">
        <span v-if="!isLive && verdictChanged" class="qg-trend" :class="'trend-' + statusClass">
          {{ trendText }}
        </span>
        <span v-if="timeRange" class="qg-time">{{ timeRange }}</span>
      </div>
    </div>

    <!-- 最终结论摘要（失败时直接可见，不必展开） -->
    <div v-if="!isLive && summary" class="qg-summary">{{ summary }}</div>

    <!-- 展开：按验收轮次分层 -->
    <div v-if="expanded" class="qg-body">
      <div
        v-for="(r, i) in roundMsgs"
        :key="i"
        class="qg-round"
        :class="'qg-round-' + roundStatus(r)"
      >
        <div class="qg-round-head" @click="toggleRound(i)">
          <span class="qg-round-caret">{{ openRounds.has(i) ? '▾' : '▸' }}</span>
          <span class="qg-round-label">第 {{ i + 1 }} 次</span>
          <span class="qg-round-badge" :class="'badge-' + roundStatus(r)">
            {{ roundStatusText(r) }}
          </span>
          <span class="qg-round-steps">{{ roundSteps(r).length }} 步</span>
          <span class="qg-round-time">{{ roundTimeRange(r) }}</span>
        </div>

        <div v-if="openRounds.has(i)" class="qg-round-body">
          <div
            v-for="(s, si) in roundSteps(r)"
            :key="si"
            class="qg-step-row"
            :class="'qa-' + (s.status || 'ok')"
          >
            <span class="qg-step-idx">{{ si + 1 }}</span>
            <span class="qg-step-icon">{{ stepIcon(s.status) }}</span>
            <span class="qg-step-action">{{ stepLabel(s) }}</span>
            <span v-if="s.selector" class="qg-step-selector">{{ s.selector }}</span>
            <span v-if="s.value" class="qg-step-value">= {{ s.value }}</span>
            <span v-if="s.detail" class="qg-step-detail">{{ s.detail }}</span>
            <span v-if="s.timestamp" class="qg-step-time">{{ shortTime(s.timestamp) }}</span>
          </div>
          <div v-if="!roundSteps(r).length" class="qg-round-empty">本轮无步骤记录</div>
          <div
            v-if="roundStatus(r) === 'fail' && (r.qa_result || {}).summary"
            class="qg-round-summary"
          >{{ (r.qa_result || {}).summary }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { DialogueMessage } from '@/types/api'

const props = defineProps<{ msg: DialogueMessage }>()

const expanded = ref(false)
const openRounds = ref(new Set<number>())

const roundMsgs = computed<any[]>(() => (props.msg as any).rounds || [])
// 卡头展示的是「最新一次验收」的结论 —— 同一 AC 修了几轮之后，
// 用户最想知道的是现在到底过了没有，而不是第一次的结果。
const latest = computed<any>(() => roundMsgs.value[roundMsgs.value.length - 1] || props.msg)
const ac = computed(() => latest.value?.qa_result || (props.msg as any).qa_result || {})
const acId = computed(() => ac.value.ac_id || '')
const label = computed(() => ac.value.label || '')
const summary = computed(() => ac.value.summary || '')
const isLive = computed(() => (props.msg as any).live === true)
// 角色全名（"Catherine（质量工程师）"）在 40% 宽的会话面板里会把卡头挤折行，
// 聚合卡只留名字本身，全名放 title 里。
const shortName = computed(() => {
  const n = String(props.msg.name || 'Catherine（质量工程师）')
  return n.split(/[（(]/)[0].trim() || 'Catherine'
})

const statusClass = computed(() => {
  if (isLive.value) return 'running'
  return String(ac.value.status || 'ok')
})

const statusText = computed(() => STATUS_TEXT[statusClass.value] || statusClass.value)

// 结论翻转（先失败后通过 / 先通过后失败）是修复循环的关键信号，值得单独说明
const verdictChanged = computed(() => {
  const sts = roundMsgs.value.map((r) => String(r?.qa_result?.status || 'ok'))
  return new Set(sts).size > 1
})
const trendText = computed(() => {
  const sts = roundMsgs.value.map((r) => String(r?.qa_result?.status || 'ok'))
  const okCount = sts.filter((s) => s === 'ok').length
  const failCount = sts.filter((s) => s === 'fail' || s === 'error').length
  const parts: string[] = []
  if (okCount) parts.push(`${okCount} 次通过`)
  if (failCount) parts.push(`${failCount} 次未通过`)
  return parts.join(' · ')
})

const timeRange = computed(() => {
  const first = roundMsgs.value[0]
  const last = roundMsgs.value[roundMsgs.value.length - 1]
  const s = shortTime(first?.qa_result?.start_ts ?? first?.timestamp)
  const e = shortTime(last?.qa_result?.end_ts ?? last?.timestamp)
  if (s && e) return s === e ? s : `${s} – ${e}`
  return s || e
})

// 进行中时自动展开到最新一轮，让步骤实时可见；结束后收起避免刷屏
watch(isLive, (v) => {
  if (v) {
    expanded.value = true
    openRounds.value = new Set([roundMsgs.value.length - 1])
  }
})

const STATUS_TEXT: Record<string, string> = {
  running: '验收中', ok: '通过', fail: '未通过', error: '执行异常', na: '不适用',
}

function roundStatus(r: any): string {
  return String(r?.qa_result?.status || 'ok')
}
function roundStatusText(r: any): string {
  const s = roundStatus(r)
  return STATUS_TEXT[s] || s
}
function roundSteps(r: any): any[] {
  return r?.qa_result?.steps || []
}
function roundTimeRange(r: any): string {
  const s = shortTime(r?.qa_result?.start_ts ?? r?.timestamp)
  const e = shortTime(r?.qa_result?.end_ts)
  if (s && e) return `${s} – ${e}`
  return s || e
}

function toggleRound(i: number) {
  const s = new Set(openRounds.value)
  s.has(i) ? s.delete(i) : s.add(i)
  openRounds.value = s
}

function shortTime(ts?: string | null): string {
  if (!ts) return ''
  const m = String(ts).match(/(\d{2}:\d{2}:\d{2})/)
  return m ? m[1] : String(ts)
}

const ACTION_LABELS: Record<string, string> = {
  click: '点击', type: '输入', select: '选择', press: '按键', wait: '等待',
  screenshot: '截图', assert_exists: '断言存在', assert_visible: '断言可见',
  assert_text: '断言文本', assert_count: '断言数量', assert_value: '断言值',
  assert_canvas_change: '断言画面变化', assert_dom_change: '断言界面更新',
}
function stepLabel(s: any): string {
  const a = s?.action || ''
  return ACTION_LABELS[a] || a
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
.qa-group-card {
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px;
  background: var(--surface, #fff);
  overflow: hidden;
}
.qa-group-fail { border-left: 3px solid #ef4444; }
.qa-group-error { border-left: 3px solid #f59e0b; }
.qa-group-na { border-left: 3px solid #9ca3af; }
.qa-group-ok { border-left: 3px solid #22c55e; }
.qa-group-running { border-left: 3px solid #3b82f6; }

.qg-toggle {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 8px 10px;
  cursor: pointer;
  user-select: none;
}
.qg-toggle:hover { background: var(--hover, #f9fafb); }

/* 主行：谁 + 哪条 AC + 标签 + 最终结论 */
.qg-main {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  min-width: 0;
}
/* 次行：验收轮次的弱信息（趋势 / 时间跨度），缩进后与主行文字对齐 */
.qg-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-left: 20px;
  font-size: 12px;
  color: var(--muted, #6b7280);
  min-width: 0;
  flex-wrap: wrap;
}

.qg-icon { color: var(--muted, #6b7280); width: 12px; flex-shrink: 0; }
.qg-name {
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  white-space: nowrap;
}
.qg-role-icon { font-size: 12px; }
.qg-ac {
  color: var(--muted, #6b7280);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.qg-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted, #6b7280);
}
.qg-time {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
/* 结论发生过翻转（先失败后通过 / 先通过后失败）是修复循环的关键信号 */
.qg-trend { white-space: nowrap; }
.qg-trend.trend-ok { color: #15803d; }
.qg-trend.trend-fail { color: #b91c1c; }
.qg-live {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #3b82f6;
  flex-shrink: 0;
}
.qg-live-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: #3b82f6;
  animation: qg-pulse 1.2s ease-in-out infinite;
}
@keyframes qg-pulse { 0%,100% { opacity: 1 } 50% { opacity: .3 } }

.qg-badge {
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 10px;
  white-space: nowrap;
  flex-shrink: 0;
}
.badge-ok { background: #dcfce7; color: #15803d; }
.badge-fail { background: #fee2e2; color: #b91c1c; }
.badge-error { background: #fef3c7; color: #b45309; }
.badge-na { background: #f3f4f6; color: #4b5563; }
.badge-running { background: #dbeafe; color: #1d4ed8; }

.qg-summary {
  padding: 0 10px 8px 20px;
  font-size: 12px;
  color: #b91c1c;
  line-height: 1.5;
}

.qg-body {
  border-top: 1px dashed var(--border, #e5e7eb);
  padding: 6px 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* 每一轮验收：与 CoderTurnCard 的轮次层同构（左侧竖线分层） */
.qg-round { border-left: 2px solid var(--border, #e5e7eb); padding-left: 8px; }
.qg-round-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  padding: 3px 0;
  cursor: pointer;
  user-select: none;
}
.qg-round-head:hover { background: var(--hover, #f9fafb); }
.qg-round-caret { color: var(--muted, #6b7280); width: 12px; }
.qg-round-label { font-weight: 600; color: var(--fg, #111827); }
.qg-round-badge { font-size: 11px; padding: 1px 6px; border-radius: 10px; }
.qg-round-steps { color: var(--muted, #6b7280); }
.qg-round-time {
  margin-left: auto;
  color: #9ca3af;
  font-variant-numeric: tabular-nums;
}
.qg-round-body {
  border-top: 1px dashed var(--border, #e5e7eb);
  padding: 4px 0 4px 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.qg-round-empty, .qg-round-summary { font-size: 11px; color: var(--muted, #6b7280); }
.qg-round-summary { color: #b91c1c; }

.qg-step-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  padding: 2px 0;
  line-height: 1.5;
}
.qg-step-idx {
  color: var(--muted, #9ca3af);
  min-width: 18px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.qg-step-action { font-weight: 500; }
.qg-step-selector {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: var(--muted, #6b7280);
}
.qg-step-value, .qg-step-detail { color: var(--muted, #6b7280); font-size: 11px; }
.qg-step-time {
  margin-left: auto;
  font-size: 11px;
  color: #9ca3af;
  font-variant-numeric: tabular-nums;
}
.qa-fail .qg-step-action, .qa-fail .qg-step-detail { color: #b91c1c; }
.qa-error .qg-step-action, .qa-error .qg-step-detail { color: #b45309; }
</style>
