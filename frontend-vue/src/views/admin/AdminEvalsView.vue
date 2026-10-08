<template>
  <AdminShell title="评测集" :subtitle="subtitle">
    <template #actions>
      <button class="refresh-btn" :disabled="loading" @click="load">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" />
        </svg>
        {{ loading ? '刷新中' : '刷新' }}
      </button>
    </template>

    <div v-if="errorMsg" class="error-bar">
      {{ errorMsg }}
      <button class="retry-link" @click="load">重试</button>
    </div>

    <!-- 空态要把「怎么才有数据」说清楚：评测不在线上跑，生产环境本来就该是空的 -->
    <div v-if="!loading && !runs.length && !errorMsg" class="empty-panel">
      <p class="empty-title">暂无评测运行</p>
      <p class="empty-hint">
        每次 <code>python eval/run_eval.py</code> 会在 <code>eval/runs/</code> 下留一个目录，
        内含过程库（<code>trace.db</code>）与结果摘要（<code>run.json</code>）。
        只保留最近 5 次。
      </p>
    </div>

    <div v-else-if="loading && !runs.length" class="panel">
      <div v-for="i in 4" :key="i" class="sk-row">
        <div class="sk-line w40"></div><div class="sk-line w15"></div>
        <div class="sk-line w10"></div><div class="sk-line w15"></div>
      </div>
    </div>

    <div v-else-if="runs.length" class="panel">
      <div class="table-scroll">
        <table class="eval-table">
          <thead>
            <tr>
              <th>运行</th>
              <th class="c">通过率</th>
              <th class="r">总耗时</th>
              <th>模型</th>
              <th>配置</th>
              <th class="c">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in runs" :key="r.run_id" class="row-link"
                @click="open(r)">
              <td class="run-cell">
                <span class="run-id mono">{{ r.run_id }}</span>
                <span class="run-meta">
                  {{ fmtFull(r.started_at) }}
                  <template v-if="r.args.tasks && r.args.tasks.length">
                    · 指定 {{ r.args.tasks.length }} 题
                  </template>
                </span>
              </td>
              <td class="c">
                <!-- 通过率是这张表的主指标：数字 + 横条，一眼看出这一轮比上一轮好还是差 -->
                <div v-if="r.running" class="rate-cell">
                  <!-- 正在跑：结果还没定论。这里绝不能显示「0/1 = 0%」——
                       那不是成绩，是「还没跑到」。 -->
                  <span class="live-dot"></span>
                  <span class="rate-running">进行中</span>
                  <!-- 「已跑完」而不是「已开始」：串行评测里已开始含正跑着的那题，
                       把它算进进度会让人以为进度比实际快一题 -->
                  <span class="rate-pct">
                    {{ r.totals.finished ?? 0 }} 题已跑完<template
                      v-if="r.totals.passed"> · {{ r.totals.passed }} 通过</template>
                  </span>
                </div>
                <div v-else class="rate-cell">
                  <span class="rate-num" :class="rateTone(r)">
                    {{ r.totals.passed ?? 0 }} / {{ r.totals.total ?? 0 }}
                  </span>
                  <span class="rate-bar">
                    <span class="rate-fill" :class="rateTone(r)"
                          :style="{ width: ratePct(r) + '%' }"></span>
                  </span>
                  <span class="rate-pct">{{ ratePct(r).toFixed(1) }}%</span>
                </div>
              </td>
              <td class="r mono">{{ r.running ? '—' : fmtDuration((r.duration_s ?? 0) * 1000) }}</td>
              <td class="mono dim">{{ r.model || '—' }}</td>
              <td>
                <div class="cfg-chips">
                  <span v-if="r.running" class="chip chip-running"
                        title="这次运行还在进行中，下面的过程与进度是实时的">
                    进行中
                  </span>
                  <!-- 「部分题」必须最靠前：一次只跑了 2 题的运行，通过率 50%
                       会和 21/21 的 100% 并排 —— 不标出来就是一次误读成「退步」。 -->
                  <span v-if="partial(r)" class="chip chip-partial"
                        :title="`这次只跑了 ${r.totals.total ?? 0} 题${r.full_set_size ? `，评测集共 ${r.full_set_size} 题` : ''}`">
                    部分题 {{ r.totals.total ?? 0 }}{{ r.full_set_size ? `/${r.full_set_size}` : '' }}
                  </span>
                  <span v-if="!r.running" class="chip" :class="r.args.with_plan ? 'chip-on' : 'chip-off'">
                    {{ r.args.with_plan ? '真实规划' : '跳过规划' }}
                  </span>
                  <span v-if="r.args.with_memory" class="chip chip-mem">记忆注入</span>
                  <template v-if="r.compare">
                    <span class="chip" :class="(r.compare.regressed ?? 0) > 0 ? 'chip-bad' : 'chip-ok'">
                      {{ (r.compare.regressed ?? 0) > 0 ? `回归 ${r.compare.regressed}` : '无回归' }}
                    </span>
                    <span v-if="r.compare.improved" class="chip chip-ok">
                      改善 {{ r.compare.improved }}
                    </span>
                  </template>
                  <span v-if="!r.has_trace" class="chip chip-off" title="这次运行没有落过程库">
                    仅结果
                  </span>
                </div>
              </td>
              <td class="c">
                <span v-if="r.has_trace" class="open-link">查看过程 →</span>
                <span v-else class="muted" title="这次运行没有落过程库，只能看结果摘要">无过程数据</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </AdminShell>
</template>

<script setup lang="ts">
/**
 * 评测运行列表。
 *
 * 数据全部来自 `/api/admin/evals`（读 `eval/runs/<run_id>/run.json`）。
 * 这里**不解析报告、不开数据库** —— 列表只回答「跑过几次、每轮什么成绩」，
 * 过程细节留给详情页按需加载。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AdminShell from '@/components/admin/AdminShell.vue'
import { adminFetch } from '@/composables/useAdmin'
import { fmtDuration, fmtFull } from '@/utils/format'

interface RunArgs {
  with_plan?: boolean
  with_memory?: boolean
  no_preview?: boolean
  tasks?: string[]
}

interface CompareInfo {
  baseline_run?: string
  improved?: number
  regressed?: number
  /** 从过变挂的题号 —— 这是对比里唯一需要立刻看的信号 */
  regressed_ids?: string[]
}

interface RunRow {
  run_id: string
  started_at: string | null
  finished_at: string | null
  duration_s: number | null
  model: string | null
  args: RunArgs
  totals: { total?: number; passed?: number; pass_rate?: number; duration_s?: number
            /** 仅进行中的运行有：已跑完的题数（「已开始」含正跑着的那题） */
            finished?: number }
  /** 当前评测集的全量题数（后端下发）。缺失时无法判定是否部分跑 */
  full_set_size?: number
  report_path: string
  compare: CompareInfo | null
  /** 这次运行还在进行中（后端从 trace.db 反推，此时还没有 run.json） */
  running?: boolean
  has_trace: boolean
}

const router = useRouter()
const runs = ref<RunRow[]>([])
const loading = ref(false)
const errorMsg = ref('')

const subtitle = computed(() => {
  if (!runs.value.length) return '自然语言需求 → 可运行应用的端到端验收结果'
  const base = `最近 ${runs.value.length} 次运行 · 每轮保留过程与结果`
  return base
})

function ratePct(r: RunRow): number {
  const t = r.totals
  if (typeof t.pass_rate === 'number') return t.pass_rate
  const total = t.total ?? 0
  return total ? (t.passed ?? 0) / total * 100 : 0
}

// 三档而不是「全过/有挂」两档：19/21(90.5%) 与 2/7(28.6%) 同色的话，
// 一眼扫过去分不出哪个是「差一点」哪个是「没跑起来」—— 而这两件事的处理方式
// 完全不同（前者看回归，后者看环境）。80% 是「还剩几道题」与「整体不达标」的
// 分界，取整数便于口头沟通。
function rateTone(r: RunRow): string {
  const pct = ratePct(r)
  const total = r.totals.total ?? 0
  if (total && r.totals.passed === total) return 'ok'
  return pct >= 80 ? 'warn' : 'bad'
}

/**
 * 这次运行是否只跑了一部分题。
 *
 * 两种来源都要认：`args.tasks` 非空 = 跑的时候显式指定了子集；
 * `full_set_size` 更大 = 历史报告里就是部分题（补历史时由后端标注）。
 * 不认出来的话，`2/7 = 28.6%` 会和 `21/21 = 100%` 在同一列里并排，
 * 读起来就是「成绩掉了一大截」。
 */
function partial(r: RunRow): boolean {
  // 进行中的运行必然会「总题数 < 全量」——那是还没跑到，不是只跑了一部分
  if (r.running) return false
  if (r.args.tasks?.length) return true
  const total = r.totals.total ?? 0
  return !!r.full_set_size && total > 0 && total < r.full_set_size
}

function open(r: RunRow) {
  if (!r.has_trace) return
  router.push(`/admin/evals/${r.run_id}`)
}

/**
 * 有运行在跑时自动刷新。
 *
 * 一次全量评测要一个多小时，页面停在旧快照上等于没有实时可言。只在**确实有
 * 进行中的运行**时轮询，跑完就自动停 —— 不给一个空转的定时器。
 */
const hasRunning = computed(() => runs.value.some(r => r.running))
let timer: number | undefined

function syncPolling() {
  const need = hasRunning.value
  if (need && timer === undefined) {
    timer = window.setInterval(load, 10000)
  } else if (!need && timer !== undefined) {
    window.clearInterval(timer)
    timer = undefined
  }
}

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    const d = await adminFetch<{ runs: RunRow[] }>('/api/admin/evals')
    runs.value = d.runs
  } catch (e) {
    errorMsg.value = (e as Error).message || '加载失败'
    runs.value = []
  } finally {
    loading.value = false
    syncPolling()
  }
}

onMounted(load)
onUnmounted(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<style scoped>
.panel {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; overflow: hidden;
}
.table-scroll { overflow-x: auto; }
.eval-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.eval-table th {
  text-align: left; font-weight: 600; font-size: 11.5px; letter-spacing: .04em;
  color: var(--faint); text-transform: uppercase;
  padding: 11px 14px; border-bottom: 1px solid var(--border);
  background: color-mix(in oklab, var(--accent-soft) 40%, transparent);
}
.eval-table td {
  padding: 12px 14px; border-bottom: 1px solid var(--border);
  color: var(--fg); vertical-align: middle;
}
.eval-table tbody tr:last-child td { border-bottom: none; }
.c { text-align: center; }
.r { text-align: right; }
.mono { font-variant-numeric: tabular-nums; font-family: var(--font-mono); font-size: 12px; }
.dim { color: var(--muted); }
.muted { color: var(--faint); }
.row-link { cursor: pointer; transition: background .12s; }
.row-link:hover { background: var(--accent-soft); }

/* 运行标识：run_id 等宽字体便于比对，时间放下一行不与它抢宽度 */
.run-cell { display: flex; flex-direction: column; gap: 3px; }
.run-id { font-weight: 600; color: var(--fg); }
.run-meta { font-size: 11.5px; color: var(--faint); }

/* 通过率：数字 + 横条 + 百分比三段，横条让「比上一轮好还是差」不用读数字 */
.rate-cell {
  display: inline-flex; align-items: center; gap: 8px; justify-content: center;
}
.rate-num {
  font-variant-numeric: tabular-nums; font-weight: 600; white-space: nowrap;
  min-width: 52px; text-align: right;
}
.rate-num.ok { color: var(--color-success); }
.rate-num.warn { color: var(--color-warning); }
.rate-num.bad { color: var(--color-danger); }
.rate-bar {
  width: 84px; height: 6px; border-radius: 999px; overflow: hidden;
  background: var(--accent-soft); flex-shrink: 0;
}
.rate-fill { display: block; height: 100%; border-radius: 999px; }
.rate-fill.ok { background: var(--color-success); }
.rate-fill.warn { background: var(--color-warning); }
.rate-fill.bad { background: var(--color-danger); }
.rate-pct {
  font-size: 11.5px; color: var(--faint); font-variant-numeric: tabular-nums;
  min-width: 44px; text-align: left;
}

/* 进行中：活动色 + 呼吸点。「还在跑」和「跑完了但结果差」是两件事，
   配色必须与 ok/warn/bad 三档区分开，否则会被当成一次失败成绩。 */
.rate-running { font-weight: 600; color: var(--accent); white-space: nowrap; }
.live-dot {
  width: 7px; height: 7px; border-radius: 50%; background: var(--accent);
  flex-shrink: 0; animation: live-pulse 1.6s ease-in-out infinite;
}
@keyframes live-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: .35; transform: scale(.82); }
}

.chip-running {
  background: color-mix(in oklab, var(--accent) 16%, transparent);
  color: var(--accent); font-weight: 600;
}

.cfg-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.chip {
  display: inline-block; padding: 2px 7px; border-radius: 5px;
  font-size: 11px; font-weight: 500; white-space: nowrap;
}
.chip-on { background: color-mix(in oklab, var(--color-success) 14%, transparent); color: var(--color-success); }
.chip-off { background: var(--accent-soft); color: var(--muted); }
.chip-mem { background: color-mix(in oklab, var(--color-role-agent) 16%, transparent); color: var(--color-role-agent); }
/* 部分题：警示色 —— 它改变的是旁边那个百分比该怎么读 */
.chip-partial {
  background: var(--color-warning-soft); color: var(--color-warning);
  font-weight: 600;
}
.chip-ok { background: color-mix(in oklab, var(--color-success) 14%, transparent); color: var(--color-success); }
.chip-bad { background: var(--color-danger-soft); color: var(--color-danger); }

.open-link { font-size: 12px; color: var(--accent); white-space: nowrap; }

.error-bar {
  background: var(--color-danger-soft);
  border: 1px solid color-mix(in oklab, var(--color-danger) 35%, transparent);
  color: var(--color-danger);
  padding: 10px 14px; border-radius: 9px;
  margin-bottom: 12px; font-size: 13px; display: flex; gap: 10px; align-items: center;
}
.retry-link {
  background: none; border: none; color: var(--color-danger);
  text-decoration: underline; cursor: pointer; font-size: 13px; font-family: inherit;
}

.empty-panel {
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  padding: 40px 44px; text-align: center; color: var(--muted);
}
.empty-title { font-size: 14px; font-weight: 600; color: var(--fg); margin-bottom: 8px; }
.empty-hint { font-size: 12.5px; color: var(--muted); line-height: 1.7; }
.empty-hint code {
  background: var(--accent-soft); padding: 1px 5px; border-radius: 4px;
  font-size: 11.5px; font-family: var(--font-mono);
}

.refresh-btn {
  display: inline-flex; align-items: center; gap: 5px; padding: 6px 12px;
  border: 1px solid var(--border); border-radius: 8px; background: var(--surface);
  font-size: 12.5px; cursor: pointer; font-family: inherit; color: var(--fg);
}
.refresh-btn:disabled { opacity: .5; cursor: not-allowed; }

.sk-row { display: flex; gap: 18px; padding: 14px; }
.sk-line { height: 11px; border-radius: 5px; background: var(--accent-soft); }
.w10 { width: 10%; } .w15 { width: 15%; } .w40 { width: 40%; }
</style>
