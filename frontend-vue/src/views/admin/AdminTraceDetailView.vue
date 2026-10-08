<template>
  <AdminShell :title="pageTitle" :subtitle="pageSubtitle">
    <template #actions>
      <!-- 状态与修复结论直接顶在标题右侧：这两个问题的答案决定要不要继续往下看 -->
      <span v-for="c in headChips" :key="c.text" class="head-tag" :class="c.tone">
        {{ c.text }}
      </span>
      <!-- trace_id 贯穿整个 run，是跨表/跨日志把一次执行串起来的唯一线索 -->
      <button v-if="traceId" class="back-btn" :title="traceId"
              @click="copy(traceId, 'trace')">
        {{ copyLabel('trace', '复制 trace_id') }}
      </button>
      <button class="back-btn" @click="router.push('/admin/traces')">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="m15 6-6 6 6 6" />
        </svg>
        返回列表
      </button>
    </template>

    <div v-if="errorMsg" class="error-bar">{{ errorMsg }}</div>

    <!-- ===== 轨迹汇总：整个需求的总量，不随轮次切换而变 =====
         轮次切换器管的是「看哪一轮」，这一行管的是「总共多少」——两者放一起会
         被误读成「当前轮次的量」，所以标题写清是需求级。 -->
    <div v-if="summary" class="sum-wrap">
      <div class="sum-head">
        <span class="sum-title">需求汇总</span>
        <span class="sum-note">整个需求，不随下方轮次切换而变</span>
      </div>
      <div class="sum-row">
        <div class="sum-card">
          <span class="sum-label">总耗时</span>
          <span class="sum-value">{{ fmtDuration(summary.duration_ms) }}</span>
          <!-- 首调用 + 长尾 = 总耗时。两个数字相加对得上，才说明取的是同一套口径。
               另外这里如实叫「首调用」：我们没有 TTFT 埋点，拿不到真正的首 Token。 -->
          <span class="sum-sub">
            首调用 {{ fmtDuration(summary.first_llm_ms) }} · 长尾 {{ fmtDuration(summary.tail_ms) }}
          </span>
        </div>

        <div class="sum-card">
          <span class="sum-label">LLM 调用</span>
          <span class="sum-value">{{ summary.llm_calls }}</span>
          <span class="sum-sub">{{ stageSummary }}</span>
        </div>

        <div class="sum-card">
          <span class="sum-label">总 Token</span>
          <span class="sum-value">{{ summary.tokens > 0 ? fmtCount(summary.tokens) : '—' }}</span>
          <span class="sum-sub">
            <template v-if="summary.tokens > 0">
              in {{ fmtCount(summary.tokens_in) }} · out {{ fmtCount(summary.tokens_out) }}
            </template>
            <template v-else>仅部分调用记录 usage</template>
          </span>
          <!-- 缓存命中单独一行：它是 in 的**子集**，塞进上一行会被读成第三类 token。
               分母只含上报过缓存信息的调用，样本量一并给出（见首页同款注释）。 -->
          <span v-if="summary.cache_hit_rate != null" class="sum-sub">
            缓存命中 {{ fmtCount(summary.cached_tokens) }}
            · {{ (summary.cache_hit_rate * 100).toFixed(1) }}% of 输入
            · {{ summary.cache_reported_calls }} 次上报
          </span>
        </div>

        <div class="sum-card">
          <span class="sum-label">总成本</span>
          <span class="sum-value">{{ summary.cost > 0 ? formatCost(summary.cost) : '—' }}</span>
          <span class="sum-sub">{{ modelSummary }}</span>
        </div>

        <!-- 重试 / 修复：判定序列直接取 verify 里程碑的 verdict，不靠猜 -->
        <div class="sum-card" :class="{ alert: repairAlert }">
          <span class="sum-label">重试 / 修复</span>
          <span class="sum-value" :class="{ bad: repairAlert }">{{ repairRatio }}</span>
          <span class="sum-sub">{{ verdictText }}</span>
        </div>

        <!-- 失败事件单独占一卡：它决定要不要继续往下查，不该藏在一行小字里 -->
        <div class="sum-card" :class="{ alert: summary.error_count > 0 }">
          <span class="sum-label">失败事件</span>
          <span class="sum-value" :class="{ bad: summary.error_count > 0 }">{{ summary.error_count }}</span>
          <span class="sum-sub">{{ summary.error_count > 0 ? '时间线上已标红' : '无' }}</span>
        </div>
      </div>

      <!-- ===== 阶段时间线 =====
           按阶段聚合成横条，回答「每个阶段总共花了多久」。与左栏时间线不重复：
           那边按连续段分组，回答的是「先后发生了什么」；这里给的是总览与占比。 -->
      <div v-if="stageSegments.length" class="stage-line">
        <div class="sl-head">
          <span class="sl-title">阶段时间线</span>
          <span class="sl-range">
            {{ fmtClock(summary.started_at) }} → {{ fmtClock(summary.last_event_at) }}
          </span>
          <!-- 每段是「该阶段首末事件之差」，会互相重叠（修复后编码器重入，
               编码与验收的时间窗是叠着的），相加并不等于总耗时。不写清楚的话，
               这条按比例拼满的横条会被读成「各阶段加起来正好等于全程」。 -->
          <span v-if="stagesOverlap" class="sl-note">跨度可重叠 · 条长仅示意相对长短</span>
        </div>
        <div class="sl-bar">
          <div v-for="s in stageSegments" :key="s.stage" class="sl-seg"
               :style="{ flexGrow: s.weight, background: s.color }"
               :title="`${s.label}：${s.events} 个事件 · 跨度 ${fmtDuration(s.ms)}（${fmtClock(s.started_at)} → ${fmtClock(s.ended_at)}）`">
            <span class="sl-name">{{ s.label }}</span>
            <span class="sl-meta">
              <template v-if="s.llm_calls">{{ s.llm_calls }} calls · </template>{{ fmtDuration(s.ms) }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== 轮次切换器：一行放不下就换行，不做横向滚动 ===== -->
    <div class="turn-switcher">
      <div class="switcher-head">
        <span class="switcher-title">对话轮次</span>
        <span class="switcher-summary">
          累计 {{ turns.length }} 轮 · {{ events.length }} 个事件
        </span>
      </div>
      <div class="pill-group">
        <button
          v-for="t in turns" :key="t.turn_index"
          class="pill" :class="{ active: activeTurn === t.turn_index }"
          @click="pickTurn(t.turn_index)">
          <span class="pill-num">{{ t.turn_index + 1 }}</span>
          <span class="pill-body">
            <span class="pill-mode">{{ t.mode }}</span>
            <span class="pill-meta">
              {{ fmtTime(t.started_at) }}→{{ fmtTime(t.ended_at) }} ·
              {{ fmtDuration(t.duration_ms) }} · {{ t.llm_calls }} 次调用
            </span>
          </span>
        </button>
        <span v-if="!turns.length && !loading" class="no-turn">暂无轮次数据</span>
      </div>
    </div>

    <!-- ===== 两栏：时间线 + 事件详情 =====
         两个组件与评测页共用（TraceTimeline / EventDetailPanel）——同一条时间线
         只该有一套渲染，否则分组规则一改就会有一边漏改。 -->
    <div class="cols" ref="colsRef">
      <TraceTimeline
        :events="events" :contract="contract" :selected-id="selectedId"
        :loading="loading" :width="leftW" @select="openEvent" />

      <!-- 拖拽手柄 -->
      <div class="handle" :class="{ dragging: dragging === 1 }"
           @mousedown="startDrag($event)">
        <span class="hd"></span><span class="hd"></span><span class="hd"></span>
      </div>

      <EventDetailPanel :detail="detail" :loading="detailLoading" />
    </div>
  </AdminShell>
</template>

<script setup lang="ts">
/**
 * 需求轨迹详情 —— 汇总 + 轮次切换 + 两栏（时间线 / 事件详情）。
 *
 * 两栏本身已抽成共用组件（`components/admin/TraceTimeline` 与
 * `EventDetailPanel`），因为评测页要展示的是**同一条时间线**；本文件只负责
 * 数据加载、轮次切换与拖拽。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AdminShell from '@/components/admin/AdminShell.vue'
import EventDetailPanel from '@/components/admin/EventDetailPanel.vue'
import TraceTimeline from '@/components/admin/TraceTimeline.vue'
import { adminFetch } from '@/composables/useAdmin'
// 状态的中文名与语义档位与首页列表共用一份（见 ./statusMeta）
import { statusLabel, statusTone } from './statusMeta'
// 成本展示口径与首页列表共用一份（见 @/utils/cost）
import { formatCost } from '@/utils/cost'
import { fmtClock, fmtCount, fmtDuration, fmtFull, fmtTime } from '@/utils/format'
import type { Contract, Detail, Ev, Summary, TraceOwner, Turn } from '@/types/trace'

const route = useRoute()
const router = useRouter()
const reqId = computed(() => route.params.id as string)

const turns = ref<Turn[]>([])
const summary = ref<Summary | null>(null)
const events = ref<Ev[]>([])
const detail = ref<Detail | null>(null)
const reqInfo = ref<TraceOwner | null>(null)
const loading = ref(false)
const detailLoading = ref(false)
const errorMsg = ref('')
const activeTurn = ref(0)
const selectedId = ref<number | null>(null)

// ---- 两栏宽度（可拖拽）----
const leftW = ref(260)
const dragging = ref(0)
const colsRef = ref<HTMLElement | null>(null)

// 事件类型的中文名与配色由后端契约下发（/api/admin/traces/contract）。
// 新增阶段只改后端 event_contract.py，前端自动跟上 —— 不需要再同步两张 map。
const contract = ref<Contract | null>(null)
const stageLabel = (s: string | null) =>
  s ? (contract.value?.stages[s]?.label ?? s) : ''

// ---- 头部：标题 / 副标题 / 状态与修复结论 ----
// trace_id 随需求信息一起下发 —— 注意 /events 接口**不带**这个字段，
// 别想从事件列表里推出来。它是把这次执行在 agent_events 与 llm_traffic.log
// 之间串起来的唯一线索，头部必须能一键复制到。
const traceId = computed(() => reqInfo.value?.trace_id ?? '')

// 复制按钮的瞬时状态：{ [按钮 key]: 'ok' | 'fail' }。复制是「按了没有任何
// 视觉变化」的动作，没有这个状态就分不清「复制成功」和「按钮没反应」。
const copyState = ref<Record<string, 'ok' | 'fail'>>({})

const pageTitle = computed(() => {
  const t = reqInfo.value?.title
  return t ? `需求轨迹 / #${reqId.value} ${t}` : `需求轨迹 / #${reqId.value}`
})

const pageSubtitle = computed(() => {
  const r = reqInfo.value
  const parts: string[] = []
  if (r?.creator) parts.push(`用户 ${r.creator}`)
  if (r?.created_at) parts.push(`提交于 ${fmtFull(r.created_at)}`)
  if (summary.value) parts.push(`总耗时 ${fmtDuration(summary.value.duration_ms)}`)
  if (traceId.value) parts.push(`trace_id: ${traceId.value.slice(0, 8)}…`)
  return parts.join(' · ')
})

// 头部 chip：状态的答案 + 「修了几轮才过」的答案。这两个问题决定了
// 要不要继续往下翻时间线。
const headChips = computed(() => {
  const out: { text: string; tone: string }[] = []
  const st = reqInfo.value?.status
  if (st) out.push({ text: statusLabel(st), tone: statusTone(st) })
  const s = summary.value
  if (s && s.verify_verdicts.length) {
    out.push(s.passed
      ? {
        text: s.repair_rounds > 0 ? `修复 ${s.repair_rounds} 轮后通过` : '一次通过',
        tone: 'ok',
      }
      : { text: '最终未通过', tone: 'bad' })
  }
  return out
})

// ---- 汇总卡的副指标 ----
// 分阶段调用数：设计稿写的是「TL 1 · Coder 17」，我们用事件自带的 stage
// 分组算，调用数为 0 的阶段不列出来（列了只会让人以为它没跑）
const stageSummary = computed(() => {
  const s = summary.value
  if (!s?.stages?.length) return `工具调用 ${s?.tool_calls ?? 0} 次`
  const parts = s.stages
    .filter(x => x.llm_calls > 0)
    .map(x => `${stageLabel(x.stage)} ${x.llm_calls}`)
  return parts.length ? parts.join(' · ') : '无 LLM 调用'
})

const modelSummary = computed(() => {
  const m = summary.value?.by_model ?? []
  if (!m.length) return '未记录模型'
  const head = m.slice(0, 3).map(x => `${x.model} ${x.calls}`)
  return m.length > 3 ? `${head.join(' · ')} 等 ${m.length} 个模型` : head.join(' · ')
})

// 「修复次数 / 验收轮数」。分母为 0（老数据没有 verify 里程碑）时显示 —，
// 而不是假装 0 —— 0 会被读成「一轮就过」。
const repairRatio = computed(() => {
  const s = summary.value
  if (!s) return '—'
  if (!s.verify_verdicts.length) return `${s.repair_rounds} / —`
  return `${s.repair_rounds} / ${s.verify_verdicts.length}`
})

// 逐轮判定：把 verify 里程碑的 verdict 按发生顺序念出来。超过 4 轮只给首尾 ——
// 一行副标题放不下，也没人会去读第 5 轮的细节。
const verdictText = computed(() => {
  const v = summary.value?.verify_verdicts ?? []
  const tag = (x: string | null, i: number) =>
    `第 ${i + 1} 轮 ${x ? String(x).toUpperCase() : '未知'}`
  if (!v.length) {
    const n = summary.value?.repair_rounds ?? 0
    return n > 0 ? `修复 ${n} 轮（无判定记录）` : '无验收判定记录'
  }
  if (v.length <= 4) return v.map(tag).join(' · ')
  return [tag(v[0], 0), '…', tag(v[v.length - 1], v.length - 1)].join(' · ')
})

// 只有「最终没通过」才标红。中间某轮 FAIL 是正常流程（后面修好了），
// 把它标红会让每一次多轮修复看起来都像事故。
const repairAlert = computed(() => {
  const s = summary.value
  if (!s) return false
  return s.verify_verdicts.length > 0 && !s.passed
})

// ---- 阶段时间线 ----
// 宽度按耗时占比分配；全部阶段耗时都是 0（事件只有时间点、没有 duration）
// 时退化成等宽，而不是挤成一条看不见的线。
const stageSegments = computed(() => {
  const segs = summary.value?.stages ?? []
  if (!segs.length) return []
  const total = segs.reduce((a, x) => a + Math.max(0, x.ms), 0)
  // 按起始时间排序 —— 叫「时间线」就得有先后。后端的 stages 是按阶段枚举
  // 出的，直接拿来会变成「编码 / 规划 / 修复 / 验收」这种没有依据的顺序。
  return [...segs]
    .sort((a, b) => String(a.started_at ?? '').localeCompare(String(b.started_at ?? '')))
    .map(x => ({
      ...x,
      label: stageLabel(x.stage),
      color: contract.value?.stages[x.stage]?.color ?? 'oklch(62% 0.05 70)',
      weight: total > 0 ? Math.max(1, x.ms) : 1,
    }))
})

// 各阶段跨度之和超过总耗时 → 时间窗确实互相重叠（实测某需求四段加起来是总耗时的
// 1.19 倍）。此时横条只能示意相对长短，绝不能让人以为它在按时间轴切分全程。
const stagesOverlap = computed(() => {
  const s = summary.value
  if (!s?.stages?.length) return false
  const sum = s.stages.reduce((a, x) => a + Math.max(0, x.ms), 0)
  return sum > s.duration_ms * 1.02 + 1000
})

// ---- 数据 ----
async function loadTurns() {
  const d = await adminFetch<{
    items: Turn[]; summary?: Summary; requirement?: TraceOwner
  }>(`/api/admin/traces/${reqId.value}/turns`)
  turns.value = d.items
  // summary 是后端算的轨迹级总量。这里不拿当前轮次的 events 自己加 ——
  // 详情页每次只加载**一个轮次**的事件，前端求和只会得到那一轮的，不是整个轨迹。
  summary.value = d.summary ?? null
  // 基本信息随同一次请求下发：以前是去列表接口里翻标题，需求翻不到
  // 那一页时页面标题就空了，而「翻不到」恰恰是最需要知道标题的时候。
  reqInfo.value = d.requirement ?? null
  const first = d.items[0]?.turn_index ?? 0
  activeTurn.value = first
}

async function loadEvents() {
  const d = await adminFetch<{ items: Ev[] }>(
    `/api/admin/traces/${reqId.value}/events?turn_index=${activeTurn.value}&limit=1000`)
  events.value = d.items
}

async function loadContract() {
  try {
    contract.value = await adminFetch<Contract>('/api/admin/traces/contract')
  } catch { /* 拉不到就走本地兜底，不阻断页面 */ }
}

async function openEvent(id: number) {
  if (selectedId.value === id && detail.value) return
  selectedId.value = id
  detailLoading.value = true
  try {
    detail.value = await adminFetch<Detail>(`/api/admin/traces/events/${id}`)
  } catch (e) {
    errorMsg.value = (e as Error).message || '加载事件失败'
  } finally {
    detailLoading.value = false
  }
}

function pickTurn(t: number) {
  activeTurn.value = t
  selectedId.value = null
  detail.value = null
  loadEvents()
}

async function copy(text: string, key: string) {
  let ok = true
  try { await navigator.clipboard.writeText(text) } catch { ok = false }
  copyState.value = { ...copyState.value, [key]: ok ? 'ok' : 'fail' }
  window.setTimeout(() => {
    const next = { ...copyState.value }
    delete next[key]
    copyState.value = next
  }, 1600)
}
function copyLabel(key: string, idle: string) {
  const st = copyState.value[key]
  return st === 'ok' ? '已复制' : st === 'fail' ? '复制失败' : idle
}

// ---- 拖拽：两栏之间只有一根分隔条，调的是时间线宽度 ----
function startDrag(ev: MouseEvent) {
  dragging.value = 1
  ev.preventDefault()
  const startX = ev.clientX
  const startLeft = leftW.value
  const box = colsRef.value?.getBoundingClientRect()
  const total = box?.width ?? 1400

  const onMove = (e: MouseEvent) => {
    // 上限留出详情栏的空间 —— 时间线再宽也不该把报文挤掉
    leftW.value = Math.min(total * 0.6, Math.max(190, startLeft + e.clientX - startX))
  }
  const onUp = () => {
    dragging.value = 0
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
  }
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'col-resize'
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

onMounted(async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    await Promise.all([loadContract(), loadTurns(), loadEvents()])
  } catch (e) {
    errorMsg.value = (e as Error).message || '加载失败'
  } finally {
    loading.value = false
  }
  // 默认选中首个 LLM 调用，避免右栏长期空着
  const firstLlm = events.value.find(e => e.kind === 'llm_turn')
  if (firstLlm) openEvent(firstLlm.id)
})

onBeforeUnmount(() => { dragging.value = 0 })
</script>

<style scoped>
.back-btn {
  display: inline-flex; align-items: center; gap: 5px; padding: 6px 12px;
  border: 1px solid oklch(88% 0.02 75); border-radius: 8px; background: #fff;
  font-size: 12.5px; cursor: pointer; font-family: inherit; color: oklch(32% 0.02 60);
}
.error-bar {
  background: oklch(95% 0.05 25); border: 1px solid oklch(88% 0.08 25);
  color: oklch(45% 0.15 25); padding: 10px 14px; border-radius: 9px;
  margin-bottom: 12px; font-size: 13px;
}

/* ---- 头部 chip：状态 + 修复结论 ---- */
.head-tag {
  font-size: 11.5px; font-weight: 600; padding: 3px 10px; border-radius: 999px;
  white-space: nowrap;
}
.head-tag.ok { background: var(--color-success-soft); color: var(--color-success); }
.head-tag.warn { background: var(--color-warning-soft); color: var(--color-warning); }
.head-tag.bad { background: var(--color-danger-soft); color: var(--color-danger); }
.head-tag.info { background: color-mix(in oklab, var(--color-info) 14%, transparent); color: var(--color-info); }
.head-tag.muted { background: var(--accent-soft); color: var(--muted); }

/* ---- 阶段时间线 ---- */
/* 与左栏时间线不重复：那边是「先后发生了什么」，这里是「各阶段各占多久」 */
.stage-line { margin-top: 12px; }
.sl-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 10px; margin-bottom: 6px;
}
.sl-title { font-size: 12.5px; font-weight: 600; color: var(--fg); }
.sl-range {
  font-size: 11px; color: var(--faint);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
/* 重叠提示：不加这句，按比例拼满的横条会被读成「各阶段加起来等于全程」 */
.sl-note { font-size: 10.5px; color: var(--faint); margin-left: auto; }
.sl-bar {
  display: flex; gap: 3px; height: 46px; border-radius: 10px; overflow: hidden;
}
/* 宽度按耗时占比（flex-grow + basis 0），并给最小宽度兜底：只有一条里程碑
   事件的阶段耗时接近 0，不设下限就会缩成一条看不见的细线 */
.sl-seg {
  flex-basis: 0; min-width: 58px; padding: 7px 9px; overflow: hidden;
  display: flex; flex-direction: column; justify-content: center; gap: 2px;
  border-radius: 8px; color: #fff;
}
.sl-name {
  font-size: 11.5px; font-weight: 600; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.sl-meta {
  font-size: 10px; opacity: .85; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; font-variant-numeric: tabular-nums;
}

/* ---- 汇总指标 ---- */
.sum-wrap { margin-bottom: 14px; }
.sum-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px; }
.sum-title { font-size: 12.5px; font-weight: 600; color: var(--fg); }
.sum-note { font-size: 11px; color: var(--faint); }
.sum-row {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
  gap: 10px;
}
.sum-card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 11px;
  padding: 11px 13px; display: flex; flex-direction: column; gap: 3px;
}
.sum-card.alert { border-color: color-mix(in oklab, var(--color-danger) 45%, var(--border)); }
.sum-label { font-size: 11.5px; color: var(--muted); }
.sum-value {
  font-size: 19px; font-weight: 600; color: var(--fg); line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.sum-value.bad { color: var(--color-danger); }
.sum-sub { font-size: 10.5px; color: var(--faint); line-height: 1.45; }

/* ---- 轮次切换器 ---- */
.turn-switcher {
  background: #fff; border: 1px solid oklch(90% 0.02 75); border-radius: 12px;
  padding: 13px 16px; margin-bottom: 14px;
}
.switcher-head {
  display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px;
}
.switcher-title { font-size: 12.5px; font-weight: 600; color: oklch(38% 0.02 60); }
.switcher-summary { font-size: 11.5px; color: oklch(60% 0.02 70); }
.pill-group { display: flex; flex-wrap: wrap; gap: 7px; }
.pill {
  display: inline-flex; align-items: center; gap: 9px; padding: 7px 13px;
  border: 1px solid oklch(89% 0.02 75); border-radius: 9px; background: #fff;
  cursor: pointer; font-family: inherit; text-align: left;
  transition: border-color .14s, background .14s;
}
.pill:hover { border-color: oklch(75% 0.06 40); }
.pill.active {
  background: oklch(66% 0.15 32); border-color: oklch(66% 0.15 32);
}
.pill-num {
  width: 21px; height: 21px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11.5px; font-weight: 700; flex-shrink: 0;
  background: oklch(94% 0.03 60); color: oklch(45% 0.02 60);
}
.pill.active .pill-num { background: oklch(100% 0 0 / .28); color: #fff; }
.pill-body { display: flex; flex-direction: column; gap: 1px; }
.pill-mode { font-size: 12.5px; font-weight: 600; color: oklch(30% 0.02 60); }
.pill-meta { font-size: 10.5px; color: oklch(58% 0.02 70); }
.pill.active .pill-mode { color: #fff; }
.pill.active .pill-meta { color: oklch(100% 0 0 / .78); }
.no-turn { font-size: 12.5px; color: oklch(62% 0.02 70); }

/* ---- 两栏 ---- */
/* 必须给固定高度：没有高度约束时 flex:1 + overflow-y:auto 不生效，
   内容会把整页撑开（50 个事件 → 页面 3000px 高），两栏也各自拉长 */
.cols {
  display: flex; align-items: stretch; gap: 0;
  height: calc(100vh - 330px); min-height: 520px;
}
/* 时间线宽度由拖拽决定、不参与伸缩；详情栏吃掉剩余空间 */
.cols > :first-child { flex-shrink: 0; }

/* ---- 拖拽手柄 ---- */
.handle {
  width: 11px; flex-shrink: 0; cursor: col-resize;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 3px; border-radius: 4px; transition: background .14s;
}
.handle:hover, .handle.dragging { background: oklch(92% 0.04 45); }
.hd { width: 3px; height: 3px; border-radius: 50%; background: oklch(66% 0.02 70); }
.handle:hover .hd, .handle.dragging .hd { background: oklch(60% 0.14 32); }
</style>
