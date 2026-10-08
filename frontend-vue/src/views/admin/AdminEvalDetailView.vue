<template>
  <AdminShell :title="pageTitle" :subtitle="pageSubtitle">
    <template #actions>
      <span v-for="c in headChips" :key="c.text" class="head-tag" :class="c.tone">
        {{ c.text }}
      </span>
      <button class="back-btn" @click="router.push('/admin/evals')">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="m15 6-6 6 6 6" />
        </svg>
        返回列表
      </button>
    </template>

    <div v-if="errorMsg" class="error-bar">{{ errorMsg }}</div>

    <!-- 对比横幅：只有存在基线且确实有回归时才出现。
         「从过变挂」是唯一需要立刻看见的信号，其余降级为列表里的小 chip。 -->
    <div v-if="regressedIds.length" class="regress-bar">
      <span class="regress-title">对比基线 {{ compare?.baseline_run }}：{{ regressedIds.length }} 题从过变挂</span>
      <span class="regress-ids">
        <button v-for="tid in regressedIds" :key="tid" class="regress-chip"
                @click="pickTask(tid)">{{ tid }}</button>
      </span>
    </div>

    <!-- 本次运行的元信息：结果侧的总量，不随选中的题变化 -->
    <div v-if="run" class="sum-row">
      <div class="sum-card">
        <span class="sum-label">{{ run.running ? '进度' : '通过率' }}</span>
        <!-- 进行中时绝不能显示「0/1 = 0%」—— 那不是成绩，是「还没跑到」 -->
        <template v-if="run.running">
          <span class="sum-value live"><span class="live-dot"></span>进行中</span>
          <span class="sum-sub">
            已完成 {{ finishedCount }} 题<span
              v-if="passedCount"> · {{ passedCount }} 题通过</span>{{ run.full_set_size ? ` / 共 ${run.full_set_size} 题` : '' }}
          </span>
        </template>
        <template v-else>
          <span class="sum-value" :class="{ bad: !allPassed }">
            {{ run.totals.passed ?? 0 }} / {{ run.totals.total ?? 0 }}
          </span>
          <span class="sum-sub">{{ pctText }}</span>
        </template>
      </div>
      <div class="sum-card">
        <span class="sum-label">总耗时</span>
        <span class="sum-value">{{ run.running ? '—' : fmtDuration((run.duration_s ?? 0) * 1000) }}</span>
        <span class="sum-sub">{{ fmtFull(run.started_at) }}</span>
      </div>
      <div class="sum-card">
        <span class="sum-label">模型</span>
        <span class="sum-value model-value">{{ run.model || '—' }}</span>
        <span class="sum-sub">{{ argsText }}</span>
      </div>
      <div class="sum-card">
        <span class="sum-label">题量</span>
        <span class="sum-value">{{ run.items.length }}</span>
        <!-- 「部分题跑」必须写在题量下面：否则 `2/7 = 28.6%` 会被直接当成
             一次正式成绩，而这个数字根本不代表评测集的水平。 -->
        <span class="sum-sub">
          <template v-if="run.running">已开始 {{ total }} 题 · 结果随跑随出</template>
          <template v-else-if="isPartial">
            评测集共 {{ run.full_set_size }} 题 · 本次只跑一部分
          </template>
          <template v-else-if="failedCount">{{ failedCount }} 题未通过</template>
          <template v-else>全部通过</template>
        </span>
      </div>
    </div>

    <!-- 进行中：说清「过程实时、逐题结论随跑随出」这件事 -->
    <div v-if="run && run.running" class="live-bar">
      <span class="live-dot"></span>
      <span>这次运行正在进行中（<strong>串行</strong>：一题跑完才起下一题）。过程（时间线、调用、用量）
        与逐题进度都是<strong>实时</strong>的；已跑完的题会立刻给出结论，只有当前这一题显示「进行中」。</span>
    </div>

    <!-- 无过程库：结果摘要仍能看，但要说明白「点时间线是没有的」 -->
    <div v-if="run && !run.has_trace" class="notice-bar">
      这次运行没有落过程库（只有结果摘要）。题目通过情况见左侧列表，时间线不可用。
    </div>

    <!-- 评测的口径只有「规划 + 编码」两个阶段（验收由离线断言替代，见 docs/design/eval-observability.md）。
         不说清的话，时间线上「验收 / 修复」这两个筛选永远为空，会被读成埋点丢了数据。 -->
    <div v-if="run && run.has_trace" class="scope-note">
      评测只跑生成阶段（<strong>规划 + 编码</strong>）：产物过没过由离线断言判定，
      不经过平台那条「验收 / 修复」链路 —— 所以「验收」「修复」两个筛选在评测里不会有结果。
    </div>

    <!-- 轮次切换：评测通常是单轮，只有真出现多轮时才显示 —— 一个只有一项的
         切换器纯属噪音，还会让人以为漏了什么 -->
    <div v-if="turns.length > 1" class="turn-strip">
      <span class="ts-label">轮次</span>
      <button v-for="t in turns" :key="t.turn_index"
              class="ts-pill" :class="{ on: activeTurn === t.turn_index }"
              @click="pickTurn(t.turn_index)">
        {{ t.turn_index + 1 }} · {{ t.mode }} ·
        {{ fmtDuration(t.duration_ms) }} · {{ t.llm_calls }} 次调用
      </button>
    </div>

    <div v-if="run" class="eval-cols">
      <!-- ===== 左：题目列表 ===== -->
      <aside class="task-rail">
        <div class="rail-head">
          <span class="rail-title">题目</span>
          <div class="rail-chips">
            <button class="rchip" :class="{ on: taskFilter === '' }" @click="taskFilter = ''">
              全部 {{ run.items.length }}
            </button>
            <button class="rchip" :class="{ on: taskFilter === 'failed' }"
                    @click="taskFilter = 'failed'">
              未过 {{ failedCount }}
            </button>
          </div>
        </div>
        <div class="rail-body">
          <button
            v-for="t in visibleTasks" :key="t.id"
            class="task-item" :class="{ on: t.id === activeTaskId, failed: t.passed === false }"
            @click="pickTask(t.id)">
            <span class="ti-top">
              <span class="ti-id mono">{{ t.id }}</span>
              <span class="ti-badge" :class="badgeTone(t)">
                {{ badgeText(t) }}
              </span>
            </span>
            <span class="ti-name">{{ t.name }}</span>
            <span class="ti-meta">
              L{{ t.level }}
              <!-- 进行中的题还没有耗时 —— 显示 0.0s 会被读成「秒过」 -->
              <template v-if="t.duration_s != null"> · {{ fmtDuration(t.duration_s * 1000) }}</template>
              <template v-if="t.rounds"> · {{ t.rounds }} 轮</template>
              <template v-if="t.event_count"> · {{ t.event_count }} 事件</template>
            </span>
            <span v-if="t.failed_assertions.length" class="ti-fail">
              未达成：{{ t.failed_assertions.join(' · ') }}
            </span>
          </button>
          <div v-if="!visibleTasks.length" class="rail-empty">没有匹配的题目</div>
        </div>
      </aside>

      <!-- ===== 右：选中题目的过程（与需求轨迹页共用同一套渲染）===== -->
      <div v-if="activeTaskId" class="cols" ref="colsRef">
        <TraceTimeline
          :events="events" :contract="contract" :selected-id="selectedId"
          :loading="loading" :width="leftW" @select="openEvent" />

        <div class="handle" :class="{ dragging: dragging === 1 }"
             @mousedown="startDrag($event)">
          <span class="hd"></span><span class="hd"></span><span class="hd"></span>
        </div>

        <EventDetailPanel :detail="detail" :loading="detailLoading" />
      </div>

      <div v-else class="pub-empty">
        {{ run.has_trace ? '从左侧选一道题查看执行过程' : '本次运行没有过程数据' }}
      </div>
    </div>

    <div v-else-if="!loading && !errorMsg" class="empty-panel">运行记录不存在</div>
  </AdminShell>
</template>

<script setup lang="ts">
/**
 * 评测运行详情：左题列表 + 右过程（时间线 / 事件详情）。
 *
 * 右侧两栏是**需求轨迹页的同一套组件**（`TraceTimeline` / `EventDetailPanel`），
 * 后端三个接口的响应结构也逐字段对齐 —— 两边渲染出来的必须是一模一样的东西，
 * 否则「评测里看到的」和「线上看到的」就失去可比性，而可比性正是评测的意义。
 *
 * 本页只负责：加载运行摘要、切换题目、串起两栏的数据加载与拖拽。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AdminShell from '@/components/admin/AdminShell.vue'
import EventDetailPanel from '@/components/admin/EventDetailPanel.vue'
import TraceTimeline from '@/components/admin/TraceTimeline.vue'
import { adminFetch } from '@/composables/useAdmin'
import { fmtDuration, fmtFull } from '@/utils/format'
import type { Contract, Detail, Ev, TraceOwner, Turn } from '@/types/trace'

interface EvalTask {
  id: string
  name: string
  level: number
  /** 三态：true 通过 / false 未过 / null 还没跑出结论（运行进行中） */
  passed: boolean | null
  /** 这题跑完了没。跑完 ≠ 有结论：串行评测里后面已有别的题在跑就说明它跑完了 */
  finished: boolean
  duration_s: number | null
  rounds: number | null
  plan_used: boolean
  plan_files: string[]
  plan_error: string
  tool_sequence: string[]
  files: string[]
  workspace: string
  error: string
  failed_assertions: string[]
  event_count: number
}

interface CompareInfo {
  baseline_run?: string
  improved?: number
  regressed?: number
  improved_ids?: string[]
  regressed_ids?: string[]
}

interface RunDetail {
  run_id: string
  started_at: string | null
  finished_at: string | null
  duration_s: number | null
  model: string | null
  args: { with_plan?: boolean; with_memory?: boolean; no_preview?: boolean; tasks?: string[] }
  totals: { total?: number; passed?: number; pass_rate?: number }
  /** 当前评测集的全量题数。小于它 → 这次只跑了一部分，百分比不能当正式成绩读 */
  full_set_size?: number
  report_path: string
  compare: CompareInfo | null
  /** 这次运行还在进行中（后端从 trace.db 反推，此时还没有 run.json） */
  running?: boolean
  has_trace: boolean
  items: EvalTask[]
}

const route = useRoute()
const router = useRouter()
const runId = computed(() => route.params.runId as string)

const run = ref<RunDetail | null>(null)
const loading = ref(false)
const errorMsg = ref('')

const taskFilter = ref<'' | 'failed'>('')
const activeTaskId = ref('')
const turns = ref<Turn[]>([])
const events = ref<Ev[]>([])
const detail = ref<Detail | null>(null)
const detailLoading = ref(false)
const activeTurn = ref(0)
const selectedId = ref<number | null>(null)

const contract = ref<Contract | null>(null)
const colsRef = ref<HTMLElement | null>(null)
const leftW = ref(300)
const dragging = ref(0)

// 题号 -> 该题在本次运行里的 requirement_id。评测用题号里的数字当需求号
// （后端 _task_req_id 的唯一解释），前端只负责剥出数字，不另立一套映射。
function reqIdOf(taskId: string): string {
  const m = /^t?(\d{1,4})$/i.exec(taskId || '')
  return m ? String(Number(m[1])) : ''
}

const passed = computed(() => run.value?.totals.passed ?? 0)
const total = computed(() => run.value?.totals.total ?? 0)
// 进行中时 total 是「已开始的题数」，不是「跑完的题数」—— 不能拿它下结论
const allPassed = computed(
  () => !run.value?.running && total.value > 0 && passed.value === total.value)
// passed === null 是「还没跑出结论」，不能算作未通过，否则刚开跑就显示
// 「1 题未过」这种假信号
const failedCount = computed(
  () => (run.value?.items ?? []).filter(t => t.passed === false).length)
// 已跑完的题数（含「跑完了但结论还没落盘」）。进行中时这才是真实进度 ——
// 用「已开始题数」当进度会把正在跑的那题也算成已完成。
const finishedCount = computed(
  () => (run.value?.items ?? []).filter(t => t.finished).length)
// 已跑完**且**有结论且通过的题数。进行中时读法是「目前已通过几题」，不是最终成绩
const passedCount = computed(
  () => (run.value?.items ?? []).filter(t => t.passed === true).length)

const pctText = computed(() => {
  const t = run.value?.totals
  if (!t) return ''
  const p = typeof t.pass_rate === 'number' ? t.pass_rate : (total.value ? passed.value / total.value * 100 : 0)
  return `${p.toFixed(1)}% 通过`
})

const compare = computed(() => run.value?.compare ?? null)
const regressedIds = computed(() => compare.value?.regressed_ids ?? [])

/**
 * 只跑了一部分题（显式 --tasks，或历史报告本身就是子集）。
 * 判据与列表页一致 —— 两处若各判一套，同一份 run.json 会给出两个结论。
 */
const isPartial = computed(() => {
  const r = run.value
  if (!r) return false
  // 进行中的运行必然是「已开始题数 < 全量」—— 那是还没跑到，不是只跑了一部分
  if (r.running) return false
  if (r.args.tasks?.length) return true
  const total = r.totals.total ?? 0
  return !!r.full_set_size && total > 0 && total < r.full_set_size
})

const argsText = computed(() => {
  const a = run.value?.args
  if (!a) return ''
  const parts: string[] = []
  // 只在**确实知道**时才写：进行中的运行参数还没落盘，`undefined` 被当成
  // 「跳过规划」就会给出一个与事实相反的结论
  if (typeof a.with_plan === 'boolean') {
    parts.push(a.with_plan ? '真实规划' : '跳过规划')
  }
  if (a.with_memory) parts.push('记忆注入')
  if (a.no_preview) parts.push('跳过预览')
  if (a.tasks?.length) parts.push(`指定 ${a.tasks.length} 题`)
  return parts.join(' · ')
})

const visibleTasks = computed(() => {
  const items = run.value?.items ?? []
  return taskFilter.value === 'failed' ? items.filter(t => t.passed === false) : items
})

/**
 * 题目状态徽标：三态（通过 / 未过 / 结论未知）。
 * 结论未知还要再分两种 —— 「跑完了但结果还没落盘」和「正在跑」，都渲染成
 * 「进行中」会让一串已跑完的题看起来全在跑，看不出串行推进。
 */
function badgeText(t: EvalTask): string {
  if (t.passed !== null) return t.passed ? '通过' : '未过'
  return t.finished ? '已完成' : '进行中'
}

function badgeTone(t: EvalTask): string {
  if (t.passed !== null) return t.passed ? 'ok' : 'bad'
  return t.finished ? 'done' : 'live'
}

const pageTitle = computed(() => `评测运行 / ${runId.value}`)
const pageSubtitle = computed(() => {
  const r = run.value
  if (!r) return ''
  const parts: string[] = []
  if (r.started_at) parts.push(`开始于 ${fmtFull(r.started_at)}`)
  if (r.running) {
    parts.push(`进行中 · 已完成 ${finishedCount.value}/${total.value} 题`
      + (r.full_set_size ? `（评测集 ${r.full_set_size} 题）` : ''))
  } else {
    parts.push(isPartial.value
      ? `部分题 ${passed.value}/${total.value} 通过（评测集 ${r.full_set_size} 题）`
      : `${passed.value}/${total.value} 通过`)
  }
  if (r.model) parts.push(r.model)
  if (r.report_path) parts.push(`报告 ${r.report_path}`)
  return parts.join(' · ')
})

const headChips = computed(() => {
  const out: { text: string; tone: string }[] = []
  const r = run.value
  // 进行中：结果还没定论，报进度 + 目前已通过数 —— 「N 题未过」在此时是假的
  if (r?.running) {
    out.push({ text: '进行中', tone: 'live' })
    out.push({ text: `已完成 ${finishedCount.value} 题`, tone: 'warn' })
    if (passedCount.value) out.push({ text: `${passedCount.value} 题通过`, tone: 'ok' })
    return out
  }
  if (isPartial.value) {
    out.push({
      text: `部分题 ${total.value}/${r?.full_set_size ?? '?'}`,
      tone: 'warn',
    })
  }
  if (total.value) {
    out.push({ text: allPassed.value ? '全部通过' : `${failedCount.value} 题未过`,
               tone: allPassed.value ? 'ok' : 'bad' })
  }
  const c = compare.value
  if (c) {
    if (c.regressed) out.push({ text: `回归 ${c.regressed}`, tone: 'bad' })
    else out.push({ text: '对比基线无回归', tone: 'ok' })
  }
  return out
})

// ---- 数据加载 ----
async function loadRun() {
  loading.value = true
  errorMsg.value = ''
  try {
    run.value = await adminFetch<RunDetail>(`/api/admin/evals/${runId.value}`)
    // 默认选第一道未通过的题：来这个页面通常就是为了看挂在哪
    const firstFail = run.value.items.find(t => !t.passed)
    const target = firstFail ?? run.value.items[0]
    if (target) pickTask(target.id)
  } catch (e) {
    errorMsg.value = (e as Error).message || '加载失败'
    run.value = null
  } finally {
    loading.value = false
    syncLivePolling()
  }
}

async function loadContract() {
  try {
    contract.value = await adminFetch<Contract>('/api/admin/traces/contract')
  } catch { /* 拉不到走本地兜底，不阻断页面 */ }
}

async function loadTurns() {
  const rid = reqIdOf(activeTaskId.value)
  if (!rid) return
  const d = await adminFetch<{ items: Turn[]; requirement?: TraceOwner }>(
    `/api/admin/evals/${runId.value}/tasks/${activeTaskId.value}/turns`)
  turns.value = d.items
  activeTurn.value = d.items[0]?.turn_index ?? 0
}

async function loadEvents() {
  const rid = reqIdOf(activeTaskId.value)
  if (!rid) return
  const d = await adminFetch<{ items: Ev[] }>(
    `/api/admin/evals/${runId.value}/tasks/${activeTaskId.value}/events`
    + `?turn_index=${activeTurn.value}&limit=1000`)
  events.value = d.items
}

async function openEvent(id: number) {
  if (selectedId.value === id && detail.value) return
  selectedId.value = id
  detailLoading.value = true
  try {
    detail.value = await adminFetch<Detail>(
      `/api/admin/evals/${runId.value}/events/${id}`)
  } catch (e) {
    errorMsg.value = (e as Error).message || '加载事件失败'
  } finally {
    detailLoading.value = false
  }
}

async function pickTask(taskId: string) {
  if (!taskId) return
  activeTaskId.value = taskId
  selectedId.value = null
  detail.value = null
  events.value = []
  turns.value = []
  if (!run.value?.has_trace) return
  loading.value = true
  try {
    await loadTurns()
    await loadEvents()
    const firstLlm = events.value.find(e => e.kind === 'llm_turn')
    if (firstLlm) openEvent(firstLlm.id)
  } catch (e) {
    errorMsg.value = (e as Error).message || '加载过程失败'
  } finally {
    loading.value = false
  }
}

function pickTurn(t: number) {
  activeTurn.value = t
  selectedId.value = null
  detail.value = null
  loadEvents()
}

// 切题目时左栏宽度不该重置 —— 那是用户的阅读偏好，不是每题的数据

// ---- 拖拽（与需求轨迹页同一交互）----
function startDrag(ev: MouseEvent) {
  dragging.value = 1
  ev.preventDefault()
  const startX = ev.clientX
  const startLeft = leftW.value
  const box = colsRef.value?.getBoundingClientRect()
  const total2 = box?.width ?? 1200
  const onMove = (e: MouseEvent) => {
    leftW.value = Math.min(total2 * 0.6, Math.max(200, startLeft + e.clientX - startX))
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

/**
 * 静默刷新：运行进行中时让页面自己跟上进度。
 *
 * 不能直接复用 `loadRun()` —— 它会「默认选第一道未通过的题」，每 15 秒把用户
 * 正在看的那道题重置掉，等于边看边被抢走视线。这里只更新数据，保留选中态。
 */
async function refreshLive() {
  const keepTask = activeTaskId.value
  const keepTurn = activeTurn.value
  try {
    run.value = await adminFetch<RunDetail>(`/api/admin/evals/${runId.value}`)
  } catch {
    return   // 偶发失败不打断阅读，下一轮再试
  }
  if (!keepTask || !run.value?.has_trace) return
  activeTaskId.value = keepTask
  try {
    await loadTurns()
    activeTurn.value = keepTurn
    await loadEvents()
  } catch { /* 同上：静默重试 */ }
}

let liveTimer: number | undefined

function syncLivePolling() {
  const need = !!run.value?.running
  if (need && liveTimer === undefined) {
    liveTimer = window.setInterval(refreshLive, 15000)
  } else if (!need && liveTimer !== undefined) {
    window.clearInterval(liveTimer)
    liveTimer = undefined
  }
}

onMounted(() => {
  loadContract()
  loadRun()
})

onUnmounted(() => {
  if (liveTimer !== undefined) window.clearInterval(liveTimer)
})
</script>

<style scoped>
.back-btn {
  display: inline-flex; align-items: center; gap: 5px; padding: 6px 12px;
  border: 1px solid var(--border); border-radius: 8px; background: var(--surface);
  font-size: 12.5px; cursor: pointer; font-family: inherit; color: var(--fg);
}
.head-tag {
  font-size: 11.5px; font-weight: 600; padding: 3px 10px; border-radius: 999px;
  white-space: nowrap;
}
.head-tag.ok { background: var(--color-success-soft); color: var(--color-success); }
.head-tag.warn { background: var(--color-warning-soft); color: var(--color-warning); }
.head-tag.bad { background: var(--color-danger-soft); color: var(--color-danger); }
/* 进行中用活动色：不是成功也不是失败，是「还没定论」 */
.head-tag.live { background: color-mix(in oklab, var(--accent) 16%, transparent); color: var(--accent); }

.error-bar {
  background: var(--color-danger-soft);
  border: 1px solid color-mix(in oklab, var(--color-danger) 35%, transparent);
  color: var(--color-danger);
  padding: 10px 14px; border-radius: 9px; margin-bottom: 12px; font-size: 13px;
}

/* ---- 对比横幅 ---- */
.regress-bar {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  background: var(--color-danger-soft);
  border: 1px solid color-mix(in oklab, var(--color-danger) 30%, transparent);
  border-radius: 10px; padding: 9px 13px; margin-bottom: 12px;
}
.regress-title { font-size: 12.5px; font-weight: 600; color: var(--color-danger); }
.regress-ids { display: flex; flex-wrap: wrap; gap: 5px; }
.regress-chip {
  padding: 2px 8px; border-radius: 999px; cursor: pointer; font-family: var(--font-mono);
  font-size: 11.5px; border: 1px solid color-mix(in oklab, var(--color-danger) 40%, transparent);
  background: transparent; color: var(--color-danger);
}
.regress-chip:hover { background: color-mix(in oklab, var(--color-danger) 12%, transparent); }

/* ---- 运行汇总 ---- */
.sum-row {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
  gap: 10px; margin-bottom: 14px;
}
.sum-card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 11px;
  padding: 11px 13px; display: flex; flex-direction: column; gap: 3px;
}
.sum-label { font-size: 11.5px; color: var(--muted); }
.sum-value {
  font-size: 19px; font-weight: 600; color: var(--fg); line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.sum-value.bad { color: var(--color-danger); }
/* 进行中：活动色 + 呼吸点。「还在跑」和「跑完了但结果差」是两件事，
   必须与 ok/warn/bad 三档区分开，否则会被当成一次失败成绩。 */
.sum-value.live {
  display: inline-flex; align-items: center; gap: 7px;
  color: var(--accent); font-size: 17px;
}
.live-dot {
  width: 8px; height: 8px; border-radius: 50%; background: var(--accent);
  flex-shrink: 0; animation: live-pulse 1.6s ease-in-out infinite;
}
@keyframes live-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: .35; transform: scale(.82); }
}
/* 模型名比数字长，降一档字号免得撑破卡片 */
.model-value { font-size: 15px; font-family: var(--font-mono); word-break: break-all; }
.sum-sub { font-size: 10.5px; color: var(--faint); line-height: 1.45; }

.notice-bar {
  font-size: 12px; color: var(--muted); background: var(--color-warning-soft);
  border: 1px solid color-mix(in oklab, var(--color-warning) 28%, transparent);
  border-radius: 9px; padding: 8px 12px; margin-bottom: 12px; line-height: 1.6;
}

/* 口径说明：不是警示也不是进行中，只是「这个页面覆盖到哪」——
   所以用最轻的中性样式，别跟上面两条抢注意力 */
.scope-note {
  font-size: 11.5px; color: var(--faint); line-height: 1.6;
  border-left: 2px solid var(--border); padding: 1px 0 1px 9px; margin-bottom: 12px;
}
.scope-note strong { color: var(--muted); font-weight: 600; }

/* 进行中提示条：用活动色而不是警示色 —— 「正在跑」不是异常 */
.live-bar {
  display: flex; align-items: center; gap: 9px;
  font-size: 12px; color: var(--fg); line-height: 1.6;
  background: color-mix(in oklab, var(--accent) 9%, transparent);
  border: 1px solid color-mix(in oklab, var(--accent) 30%, transparent);
  border-radius: 9px; padding: 8px 12px; margin-bottom: 12px;
}
.live-bar strong { color: var(--accent); }

/* ---- 轮次切换（仅多轮出现）---- */
.turn-strip {
  display: flex; align-items: center; gap: 7px; flex-wrap: wrap;
  margin-bottom: 12px;
}
.ts-label { font-size: 11.5px; color: var(--faint); }
.ts-pill {
  padding: 5px 11px; border-radius: 999px; cursor: pointer; font-family: inherit;
  font-size: 11.5px; border: 1px solid var(--border); background: var(--surface);
  color: var(--muted); transition: background .12s, color .12s, border-color .12s;
}
.ts-pill:hover { border-color: color-mix(in oklab, var(--accent) 45%, transparent); }
.ts-pill.on { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }

/* ---- 主区：左题列表 + 右过程 ---- */
.eval-cols {
  display: flex; align-items: stretch; gap: 0;
  height: calc(100vh - 370px); min-height: 520px;
}

.task-rail {
  width: 250px; flex-shrink: 0; display: flex; flex-direction: column;
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  overflow: hidden; margin-right: 12px;
}
.rail-head {
  padding: 10px 12px; border-bottom: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 8px; flex-shrink: 0;
}
.rail-title { font-size: 12.5px; font-weight: 600; color: var(--fg); }
.rail-chips { display: flex; gap: 5px; }
.rchip {
  padding: 3px 9px; border-radius: 999px; cursor: pointer; font-family: inherit;
  font-size: 11.5px; border: 1px solid var(--border); background: transparent;
  color: var(--muted); transition: background .12s, color .12s;
}
.rchip:hover { background: var(--accent-soft); color: var(--accent); }
.rchip.on { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
.rail-body { overflow-y: auto; flex: 1; padding: 6px; }
.rail-empty { padding: 20px; text-align: center; color: var(--faint); font-size: 12.5px; }

.task-item {
  width: 100%; text-align: left; cursor: pointer; font-family: inherit;
  display: flex; flex-direction: column; gap: 3px;
  padding: 8px 10px; border-radius: 9px; border: 1px solid transparent;
  background: transparent; margin-bottom: 3px;
  transition: background .12s, border-color .12s;
}
.task-item:hover { background: var(--accent-soft); }
.task-item.on { background: var(--accent-soft); border-color: color-mix(in oklab, var(--accent) 40%, transparent); }
/* 未通过的题在列表里天然标红：扫一眼就知道还有几道没搞定 */
.task-item.failed .ti-name { color: var(--color-danger); }
.ti-top { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.ti-id { font-size: 11.5px; color: var(--muted); font-weight: 600; }
.ti-badge {
  font-size: 10.5px; font-weight: 600; padding: 1px 7px; border-radius: 999px;
}
.ti-badge.ok { background: var(--color-success-soft); color: var(--color-success); }
.ti-badge.bad { background: var(--color-danger-soft); color: var(--color-danger); }
/* 进行中的题：不是失败，不能标红 */
.ti-badge.live { background: color-mix(in oklab, var(--accent) 16%, transparent); color: var(--accent); }
/* 跑完了但结论还没落盘：中性色 —— 既不是通过也不是失败，别给读者任何暗示 */
.ti-badge.done { background: color-mix(in oklab, var(--muted) 16%, transparent); color: var(--muted); }
.ti-name {
  font-size: 12.5px; font-weight: 500; color: var(--fg);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ti-meta { font-size: 10.5px; color: var(--faint); }
.ti-fail {
  font-size: 10.5px; color: var(--color-danger); line-height: 1.4;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.cols { display: flex; align-items: stretch; gap: 0; flex: 1; min-width: 0; }
.cols > :first-child { flex-shrink: 0; }
.handle {
  width: 11px; flex-shrink: 0; cursor: col-resize;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 3px; border-radius: 4px; transition: background .14s;
}
.handle:hover, .handle.dragging { background: color-mix(in oklab, var(--accent) 18%, transparent); }
.hd { width: 3px; height: 3px; border-radius: 50%; background: var(--muted); }

.pub-empty {
  flex: 1; display: flex; align-items: center; justify-content: center;
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  color: var(--muted); font-size: 13px;
}
.empty-panel {
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  padding: 44px; text-align: center; color: var(--muted); font-size: 13px;
}
.mono { font-variant-numeric: tabular-nums; font-family: var(--font-mono); }
</style>
