<template>
  <div class="turn-card">
    <!-- 折叠栏：一次编码回合（多轮迭代）= 一张卡片 -->
    <div class="turn-toggle" @click="toggleTurn">
      <span class="turn-icon">{{ expanded ? '▾' : '▸' }}</span>
      <span class="agent-name" :style="{ color: roleColor }">
        <span class="role-icon">{{ roleIcon }}</span>
        {{ displayName }}
      </span>
      <span v-if="isLive" class="turn-live">
        <span class="turn-live-dot"></span>进行中…
      </span>
      <span class="turn-meta">{{ rounds.length }} 轮 · {{ totalTools }} 个操作</span>
      <span v-if="hasThinking" class="turn-dot" title="含思考内容">💭</span>
      <span v-if="timeRange" class="turn-time">{{ timeRange }}</span>
    </div>

    <!-- 展开内容：一键开合 + 逐轮详情 -->
    <div v-if="expanded" class="turn-body">
      <div class="turn-toolbar">
        <button class="turn-btn" @click.stop="setAll(true)">展开全部</button>
        <button class="turn-btn" @click.stop="setAll(false)">收起全部</button>
        <span v-if="isLive" class="turn-live-inline">实时累积中</span>
      </div>

      <template v-for="(r, i) in rounds" :key="i">
        <!-- 阶段分隔：一张合并卡里可能含两次编码阶段（各自的 iteration 都从 1 开始），
             不插分隔就会出现两个「第 1 轮」，看着像重复渲染。 -->
        <div v-if="isPhaseBreak(i)" class="round-phase-break">
          <span class="round-phase-line"></span>
          <span class="round-phase-text">补充实现</span>
          <span class="round-phase-line"></span>
        </div>
        <div class="turn-round">
          <div class="round-head" @click="toggleRound(i)">
            <span class="round-caret">{{ openRounds.has(i) ? '▾' : '▸' }}</span>
            <span class="round-label">第 {{ i + 1 }} 轮</span>
            <span class="round-count">{{ (r.tools || []).length }} 个操作</span>
            <span v-if="r.live" class="round-live">进行中</span>
            <span v-if="r.thinking_preview" class="round-think-dot" title="有思考内容">💭</span>
            <span v-if="roundTime(r)" class="round-time">{{ roundTime(r) }}</span>
          </div>

          <div v-if="openRounds.has(i)" class="round-body">
            <div v-if="r.thinking_preview" class="round-block">
              <div class="round-block-head" @click="toggleThinking(i)">
                <span>{{ openThinking.has(i) ? '▾' : '▸' }}</span>
                <span>💭 思考</span>
                <span class="round-block-preview">{{ r.thinking_preview }}</span>
              </div>
              <div v-if="openThinking.has(i)" class="round-thinking">{{ r.thinking_preview }}</div>
            </div>

            <div v-if="r.agent_text" class="round-text">{{ r.agent_text }}</div>

            <div v-if="(r.tools || []).length" class="round-tools">
              <div v-for="(tool, ti) in r.tools" :key="ti" class="tool-item">
                <span class="tool-icon">{{ tool.success ? '✅' : tool.blocked ? '⛔' : '❌' }}</span>
                <span class="tool-label">{{ tool.readable || tool.display_label || tool.name }}</span>
                <span
                  v-if="hasToolArgs(tool)"
                  class="tool-detail"
                  @click.stop="toggleToolArgs(r, ti)"
                >{{ isToolOpen(r, ti) ? '收起 ▴' : '参数 ▸' }}</span>
              </div>
              <div v-for="key in openToolsFor(r)" :key="'arg-' + key" class="tool-args">
                <pre>{{ JSON.stringify((r.tools || [])[key]?.arguments, null, 2) }}</pre>
              </div>
            </div>
            <div v-else class="round-empty">本轮无操作记录</div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{ msg: any }>()

const ROLE_ICONS: Record<string, string> = {
  'Leon（技术负责人）': '🎯',
  'Henry（开发工程师）': '⚙️',
  'Catherine（质量工程师）': '🔍',
}
const ROLE_COLORS: Record<string, string> = {
  'Leon（技术负责人）': '#7c3aed',
  'Henry（开发工程师）': '#ea580c',
  'Catherine（质量工程师）': '#2563eb',
}

const rounds = computed<any[]>(() => props.msg?.rounds || [])
const isLive = computed(() => props.msg?.live === true)
const totalTools = computed(() =>
  rounds.value.reduce((n, r) => n + ((r.tools || []).length), 0)
)
const hasThinking = computed(() => rounds.value.some((r) => !!r.thinking_preview))

const displayName = computed(() => props.msg?.name || 'AI')
const roleIcon = computed(() => {
  const n = displayName.value
  return ROLE_ICONS[n] || ROLE_ICONS[n.split(' ')[0]] || '⚙️'
})
const roleColor = computed(() => {
  const n = displayName.value
  return ROLE_COLORS[n] || ROLE_COLORS[n.split(' ')[0]] || 'var(--accent)'
})

/** 进行中的回合默认展开，并打开最新一轮；结束后默认收起，避免刷屏 */
const expanded = ref(isLive.value)
const openRounds = ref<Set<number>>(new Set(isLive.value ? [rounds.value.length - 1] : []))
const openThinking = ref<Set<number>>(new Set())
// key = `${roundIdx}:${toolIdx}`，避免多轮之间工具序号串味
const openTools = ref<Set<string>>(new Set())

watch(isLive, (v) => {
  if (!v) return
  expanded.value = true
  const s = new Set(openRounds.value)
  s.add(rounds.value.length - 1)
  openRounds.value = s
})

watch(
  () => rounds.value.length,
  (n) => {
    if (!isLive.value || n === 0) return
    const s = new Set(openRounds.value)
    s.add(n - 1)
    openRounds.value = s
  }
)

/** 展开整张卡时默认带开「最后一轮」——否则展开后只看到一排收起的行，等于没展开 */
function toggleTurn() {
  const next = !expanded.value
  expanded.value = next
  if (!next || !rounds.value.length) return
  const s = new Set(openRounds.value)
  s.add(rounds.value.length - 1)
  openRounds.value = s
}

/** 阶段分隔判定：合并卡里可能含两次编码阶段（首轮编码 + 补充实现续跑），
 *  两阶段的 iteration 各自从 1 开始。编号回退即一次新阶段开始 ——
 *  不插分隔就会出现两个「第 1 轮」，看起来像重复渲染。 */
function isPhaseBreak(i: number): boolean {
  if (i <= 0) return false
  const cur = rounds.value[i]?.iteration
  const prev = rounds.value[i - 1]?.iteration
  if (typeof cur !== 'number' || typeof prev !== 'number') return false
  return cur <= prev
}

function toggleRound(i: number) {
  const s = new Set(openRounds.value)
  s.has(i) ? s.delete(i) : s.add(i)
  openRounds.value = s
}

function toggleThinking(i: number) {
  const s = new Set(openThinking.value)
  s.has(i) ? s.delete(i) : s.add(i)
  openThinking.value = s
}

function toggleToolArgs(r: any, ti: number | string) {
  const idx = Number(ti)
  const key = `${rounds.value.indexOf(r)}:${idx}`
  const s = new Set(openTools.value)
  s.has(key) ? s.delete(key) : s.add(key)
  openTools.value = s
}

function isToolOpen(r: any, ti: number | string) {
  return openTools.value.has(`${rounds.value.indexOf(r)}:${Number(ti)}`)
}

function openToolsFor(r: any) {
  const ri = rounds.value.indexOf(r)
  return Array.from(openTools.value)
    .filter((k) => k.startsWith(`${ri}:`))
    .map((k) => Number(k.split(':')[1]))
    .sort((a, b) => a - b)
}

/** 一键展开/收起：同时作用于「轮」与「思考」两层 */
function setAll(open: boolean) {
  if (open) {
    openRounds.value = new Set(rounds.value.map((_, i) => i))
    openThinking.value = new Set(
      rounds.value.map((r, i) => (r.thinking_preview ? i : -1)).filter((i) => i >= 0)
    )
  } else {
    openRounds.value = new Set()
    openThinking.value = new Set()
    openTools.value = new Set()
  }
}

function hasToolArgs(tool: any): boolean {
  const a = tool?.arguments
  return a !== undefined && a !== null && Object.keys(a).length > 0
}

function shortTime(ts?: string | null): string {
  if (!ts) return ''
  const m = String(ts).match(/(\d{2}:\d{2}:\d{2})/)
  return m ? m[1] : String(ts)
}

function roundTime(r: any): string {
  const s = shortTime(r?.start_ts)
  const e = shortTime(r?.end_ts ?? r?.timestamp)
  if (s && e) return `${s} – ${e}`
  return s || e
}

const timeRange = computed(() => {
  const m = props.msg || {}
  const s = shortTime(m.start_ts)
  const e = shortTime(m.end_ts ?? m.timestamp)
  if (s && e) return `${s} – ${e}`
  return s || e
})
</script>

<style scoped>
.turn-card {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}

.turn-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 8px 12px;
  transition: background 0.15s;
  font-size: 13px;
}
.turn-toggle:hover { background: var(--surface); }

.turn-icon {
  font-size: 12px;
  color: var(--muted);
  width: 14px;
  text-align: center;
  flex-shrink: 0;
}

.agent-name {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  flex-shrink: 0;
}

.role-icon { margin-right: 3px; font-size: 13px; }

.turn-meta {
  font-size: 12px;
  color: var(--muted);
  flex: 1;
  min-width: 0;
}

.turn-dot { font-size: 13px; flex-shrink: 0; }

.turn-time {
  font-size: 12px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.turn-live {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  padding: 2px 8px;
  border-radius: 999px;
  flex-shrink: 0;
}

.turn-live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: turn-live-pulse 1s infinite alternate;
}

@keyframes turn-live-pulse {
  from { opacity: 0.35; transform: scale(0.8); }
  to { opacity: 1; transform: scale(1.1); }
}

.turn-body {
  border-top: 1px solid var(--border);
  padding: 8px 12px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.turn-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 4px;
}

.turn-btn {
  font-size: 11px;
  line-height: 1;
  padding: 4px 8px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--fg);
  cursor: pointer;
}
.turn-btn:hover { border-color: var(--accent); color: var(--accent); }

.turn-live-inline {
  font-size: 11px;
  color: var(--accent);
}

/* 阶段分隔：合并卡含两次编码阶段（首轮编码 + 补充实现续跑）时标出边界。
   两阶段的 iteration 各自从 1 开始，不标边界会出现两个「第 1 轮」。 */
.round-phase-break {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0 4px 10px;
}

.round-phase-line {
  flex: 1;
  height: 1px;
  background: var(--border);
}

.round-phase-text {
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
}

.turn-round {
  border-left: 2px solid var(--border);
  padding-left: 8px;
}

.round-head {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  padding: 4px 0;
  font-size: 12px;
}

.round-caret {
  font-size: 11px;
  color: var(--muted);
  width: 12px;
  text-align: center;
  flex-shrink: 0;
}

.round-label {
  font-weight: 600;
  color: var(--fg);
  flex-shrink: 0;
}

.round-count { font-size: 11px; color: var(--muted); }

.round-live {
  font-size: 11px;
  color: var(--accent);
  flex-shrink: 0;
}

.round-think-dot { font-size: 12px; flex-shrink: 0; }

.round-time {
  margin-left: auto;
  font-size: 11px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.round-body {
  padding: 2px 0 6px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.round-block-head {
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  font-size: 12px;
  color: var(--muted);
}

.round-block-preview {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.round-thinking {
  margin-top: 4px;
  padding: 8px 10px;
  background: var(--surface);
  border-radius: 6px;
  border-left: 2px solid var(--border);
  font-size: 12px;
  line-height: 1.5;
  color: var(--muted);
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
}

.round-text {
  font-size: 12px;
  line-height: 1.6;
  color: var(--fg);
  padding: 6px 10px;
  background: var(--surface);
  border-radius: 6px;
}

.round-tools {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tool-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px;
  font-size: 12px;
  border-radius: 4px;
}
.tool-item:hover { background: var(--surface); }

.tool-icon { font-size: 12px; flex-shrink: 0; }

.tool-label {
  color: var(--fg);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-detail {
  font-size: 11px;
  color: var(--accent);
  cursor: pointer;
  flex-shrink: 0;
  user-select: none;
}

.tool-args {
  margin: 2px 0 4px 20px;
  padding: 6px 8px;
  background: var(--dark-bg);
  color: var(--dark-fg);
  border-radius: 4px;
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  white-space: pre-wrap;
  max-height: 160px;
  overflow-y: auto;
}

.round-empty {
  font-size: 11px;
  color: var(--muted);
  padding: 2px 0;
}
</style>
