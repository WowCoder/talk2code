<template>
  <AdminShell title="需求轨迹" :subtitle="subtitle">
    <template #actions>
      <button class="ghost-btn" :disabled="exporting || !total" @click="exportCsv">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 3v12M7.5 10.5 12 15l4.5-4.5" />
          <path d="M4 17v2.5h16V17" />
        </svg>
        {{ exporting ? '导出中' : '导出 CSV' }}
      </button>
      <button class="refresh-btn" :disabled="loading" @click="reload">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" />
        </svg>
        {{ loading ? '刷新中' : '刷新' }}
      </button>
    </template>

    <div v-if="errorMsg" class="error-bar">
      {{ errorMsg }}
      <button class="retry-link" @click="reload">重试</button>
    </div>

    <!-- ===== 指标卡：数字全部来自 /api/admin/traces/stats，不在这里另算 ===== -->
    <!-- 先给骨架再给数字：指标要扫全表，直接渲染会让整页跳一下 -->
    <div v-if="!stats && !errorMsg" class="kpi-row">
      <div v-for="i in 6" :key="i" class="kpi-card skeleton-card">
        <div class="sk-tile"></div>
        <div class="sk-line w50"></div>
        <div class="sk-line w70 big"></div>
        <div class="sk-line w60"></div>
      </div>
    </div>

    <div v-else-if="stats" class="kpi-row">
      <div class="kpi-card">
        <div class="kpi-head">
          <span class="kpi-tile today">
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="m5 12.5 4.5 4.5L19 7.5" />
            </svg>
          </span>
        </div>
        <span class="kpi-label">今日完成需求</span>
        <span class="kpi-value">{{ stats.today.finished }}</span>
        <span class="kpi-sub">{{ deltaText }}</span>
      </div>

      <div class="kpi-card">
        <div class="kpi-head">
          <span class="kpi-tile running">
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="8.5" />
              <path d="M12 7.5V12l3 2" />
            </svg>
          </span>
        </div>
        <span class="kpi-label">进行中<em v-if="rangeLabel" class="kpi-scope">{{ rangeLabel }}</em></span>
        <span class="kpi-value">{{ stats.active.total }}</span>
        <!-- 等待用户确认单独点出来：这类需求是「卡在人身上」而不是「卡在系统上」，
             不区分的话会以为后台还剩一堆活没干 -->
        <span class="kpi-sub">
          <template v-if="stats.active.awaiting_user > 0">
            <span class="dot-warn"></span>{{ stats.active.awaiting_user }} 个等待用户确认
          </template>
          <template v-else>无等待用户确认</template>
        </span>
      </div>

      <div class="kpi-card">
        <div class="kpi-head">
          <span class="kpi-tile duration">
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="13" r="8" />
              <path d="M12 9.5V13l2.5 1.5M9 2h6" />
            </svg>
          </span>
        </div>
        <span class="kpi-label">平均完成耗时<em v-if="rangeLabel" class="kpi-scope">{{ rangeLabel }}</em></span>
        <span class="kpi-value">{{ fmtDuration(stats.duration.avg_ms) }}</span>
        <!-- 样本数必须写出来：几十条样本上的 P95 不是稳定承诺，
             不标样本量会让人把它当成 SLA -->
        <span class="kpi-sub">
          P95 {{ fmtDuration(stats.duration.p95_ms) }} · {{ stats.duration.sample }} 个样本
        </span>
      </div>

      <div class="kpi-card">
        <div class="kpi-head">
          <span class="kpi-tile cost">
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M7 5.5h10M7 10h10M13.5 5.5c2.2 0 4 1.1 4 3s-1.8 3-4 3H9l7 7" />
            </svg>
          </span>
        </div>
        <span class="kpi-label">今日累计成本</span>
        <span class="kpi-value">{{ formatCost(stats.today.cost) }}</span>
        <span class="kpi-sub">今日 {{ fmt(stats.today.llm_calls) }} 次 LLM 调用</span>
      </div>

      <!-- 缓存命中率：命中量是 input token 的**子集**，不是并列的第三类 token。
           分母只含「上报过缓存信息」的调用，副标题把分子/分母/样本量全给出来 ——
           不给样本量的话，3 次调用算出的 99% 和 500 次算出的 60% 在界面上没区别。
           「无数据」必须与 0% 分开显示：前者是没记录，后者是查过但没命中。 -->
      <div class="kpi-card">
        <div class="kpi-head">
          <span class="kpi-tile cache">
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3.5 4.5 8v8L12 20.5 19.5 16V8z" />
              <path d="M4.5 8 12 12.5 19.5 8M12 12.5v8" />
            </svg>
          </span>
          <span v-if="stats.cache_hit_rate === null" class="kpi-chip flat">无数据</span>
        </div>
        <span class="kpi-label">缓存命中率<em v-if="rangeLabel" class="kpi-scope">{{ rangeLabel }}</em></span>
        <span class="kpi-value">
          {{ stats.cache_hit_rate === null ? '—' : (stats.cache_hit_rate * 100).toFixed(1) + '%' }}
        </span>
        <span class="kpi-sub">
          <template v-if="stats.cache_hit_rate === null">范围内没有调用上报缓存信息</template>
          <template v-else>
            {{ fmt(stats.cached_tokens) }} / {{ fmt(stats.cache_reported_tokens_in) }} 输入 token
            · {{ fmt(stats.cache_reported_calls) }} 次调用
          </template>
        </span>
      </div>

      <!-- 埋点自身的健康度：写入失败只告警不抛出，不看这个就分不清
           「后台没数据」和「这次没产生数据」 -->
      <div class="kpi-card" :class="{ alert: stats.writer_failures > 0 }">
        <div class="kpi-head">
          <span class="kpi-tile health" :class="{ bad: stats.writer_failures > 0 }">
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3.5 3.5 19h17z" />
              <path d="M12 9.5v4M12 16.2v.3" />
            </svg>
          </span>
          <span class="kpi-chip" :class="stats.writer_failures > 0 ? 'warn' : 'ok'">
            {{ stats.writer_failures > 0 ? '需关注' : '正常' }}
          </span>
        </div>
        <span class="kpi-label">埋点写入失败</span>
        <span class="kpi-value">{{ stats.writer_failures }}</span>
        <span class="kpi-sub">本次进程内累计 · 0 为正常</span>
      </div>
    </div>

    <!-- ===== 筛选条件 ===== -->
    <!-- 第一行是主筛选：状态分档 + 搜索 + 时间范围，都放在一行（与设计稿一致） -->
    <div class="filterbar">
      <div class="frow">
        <div class="fchips">
          <button class="fchip" :class="{ on: !bucketFilter }" @click="pickBucket('')">
            全部
            <b v-if="stats">{{ stats.total_requirements }}</b>
          </button>
          <button
            v-for="b in bucketChips" :key="b.key"
            class="fchip" :class="{ on: bucketFilter === b.key }"
            @click="pickBucket(b.key)">
            <span class="fdot" :style="{ background: b.color }"></span>
            {{ b.label }} <b>{{ b.count }}</b>
          </button>
        </div>

        <div class="fright">
          <div class="search-box">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
            </svg>
            <input v-model="keyword" placeholder="搜索需求编号 / 标题" @keyup.enter="reload" />
          </div>
          <div class="range-box">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="3.5" y="5" width="17" height="15.5" rx="2.5" />
              <path d="M3.5 10h17M8 3v4M16 3v4" />
            </svg>
            <!-- 四档按**创建时间**收窄（自然日对齐：「近 3 天」= 今天 + 前 2 个日历日），
                 与默认排序同一口径。窗口由后端 `_range_since` 唯一解释，前端不另算边界。 -->
            <select v-model="rangeFilter" @change="reload">
              <option value="all">全部时间</option>
              <option value="today">当天</option>
              <option value="3">近 3 天</option>
              <option value="7">近 7 天</option>
              <option value="30">近 30 天</option>
            </select>
          </div>
        </div>
      </div>

      <!-- 事件类型 chip 由后端契约下发（filter_kinds），新增事件类型时自动跟上，
           不在前端再维护一份白名单 -->
      <div class="frow">
        <span class="flabel">事件类型</span>
        <div class="fchips">
          <button class="fchip" :class="{ on: !kindFilter }" @click="pickKind('')">全部</button>
          <button
            v-for="k in kindChips" :key="k"
            class="fchip" :class="{ on: kindFilter === k }"
            @click="pickKind(k)">
            <span class="fdot" :style="{ background: kindColor(k) }"></span>
            含{{ kindLabel(k) }}
          </button>
        </div>
      </div>
    </div>

    <!-- 令牌覆盖提示：usage 只有部分调用会记录，不能假装有全量 -->
    <div class="notice-bar">
      Token 用量仅涵盖带 <code>usage</code> 字段的调用，"未记录"与"零消耗"在此不可区分。
    </div>

    <!-- 骨架 -->
    <div v-if="!rows.length && loading" class="panel skeleton-card">
      <div v-for="i in 6" :key="i" class="sk-row">
        <div class="sk-line w40"></div><div class="sk-line w15"></div>
        <div class="sk-line w10"></div><div class="sk-line w15"></div>
      </div>
    </div>

    <div v-else-if="rows.length" class="panel">
      <div class="table-scroll">
      <table class="trace-table">
        <thead>
          <tr>
            <th>需求摘要</th>
            <th class="c">状态</th>
            <th>创建人</th>
            <!-- 两列时间都可点排序，默认按创建时间倒序：跑着的需求若按「最近活跃」
                 排序会不停把自己顶到第一行，正在看的表会自己重排 -->
            <th class="sortable" :class="{ on: sortKey === 'created' }"
                @click="pickSort('created')">
              创建时间<i class="arrow">{{ sortKey === 'created' ? '↓' : '' }}</i>
            </th>
            <th class="sortable" :class="{ on: sortKey === 'active' }"
                @click="pickSort('active')">
              最近活跃<i class="arrow">{{ sortKey === 'active' ? '↓' : '' }}</i>
            </th>
            <th class="r">耗时</th>
            <th class="c">LLM 调用</th>
            <th class="r">Token</th>
            <th class="r">成本</th>
            <th class="c">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.requirement_id" class="row-link"
              @click="open(r.requirement_id)">
            <!-- 轮次标签贴在标题下（与设计稿一致）：它描述这条需求本身，
                 不适合单占一列，否则「第几轮」会和「几次调用」抢注意力 -->
            <td class="title-cell">
              <span class="req-title">{{ r.title }}</span>
              <span class="req-meta">
                <span class="chip" :class="r.turn_count > 1 ? 'chip-dialog' : 'chip-initial'">
                  {{ r.turn_count > 1 ? `${r.turn_count} 轮对话` : '初次生成' }}
                </span>
                <span class="req-id">#{{ r.requirement_id }}</span>
              </span>
            </td>
            <td class="c">
              <span class="status-dot" :style="{ background: statusColor(r.status) }"></span>
              {{ statusLabel(r.status) }}
            </td>
            <td class="mono dim">{{ r.creator || '—' }}</td>
            <td class="mono dim">{{ fmtDateTime(r.created_at) }}</td>
            <td class="mono dim">{{ relTime(r.last_event_at) }}</td>
            <td class="r mono">{{ fmtDuration(r.duration_ms) }}</td>
            <td class="c mono">{{ r.llm_calls }}</td>
            <td class="r mono">
              <template v-if="r.tokens > 0">{{ fmt(r.tokens) }}</template>
              <span v-else class="muted" title="该需求的调用未记录 usage">—</span>
            </td>
            <td class="r mono">
              <template v-if="r.cost > 0">{{ formatCost(r.cost) }}</template>
              <span v-else class="muted">—</span>
            </td>
            <td class="c"><span class="open-link">查看轨迹 →</span></td>
          </tr>
        </tbody>
      </table>
      </div>

      <div class="pager">
        <button :disabled="page <= 1" @click="go(page - 1)">上一页</button>
        <span class="page-info">第 {{ page }} / {{ maxPage }} 页</span>
        <button :disabled="page >= maxPage" @click="go(page + 1)">下一页</button>
      </div>
    </div>

    <div v-else-if="!loading" class="empty-panel">暂无轨迹数据</div>
  </AdminShell>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AdminShell from '@/components/admin/AdminShell.vue'
import { adminFetch } from '@/composables/useAdmin'
// 状态的中文名与配色与详情页共用一份（见 ./statusMeta）
import { statusColor, statusLabel } from './statusMeta'
// 成本展示口径（¥ 符号 + 精度）与其余页面共用一份（见 @/utils/cost）
import { formatCost } from '@/utils/cost'

interface TraceRow {
  requirement_id: number
  title: string
  creator: string
  bucket: string
  status: string
  turn_count: number
  event_count: number
  llm_calls: number
  tool_calls: number
  tokens: number
  cost: number
  duration_ms: number
  started_at: string | null
  last_event_at: string | null
  /** 需求创建时间（孤儿事件为 null，此时列表回退用首条事件时间） */
  created_at: string | null
  error_count: number
}

interface Stats {
  by_kind: { kind: string; count: number }[]
  by_status: { status: string; count: number }[]
  status_buckets: { key: string; count: number }[]
  total_events: number
  total_requirements: number
  requirements_total: number
  total_tokens: number
  /** cached 是 tokens_in 的**子集**，不是并列的第三类 token */
  tokens_in: number
  cached_tokens: number
  /** null = 范围内没有调用上报过缓存信息（≠ 0%，0% 是「查过、一次没命中」） */
  cache_hit_rate: number | null
  cache_reported_calls: number
  cache_reported_tokens_in: number
  total_cost: number
  last_event_at: string | null
  today: {
    finished: number; finished_prev: number
    cost: number; llm_calls: number; events: number
  }
  active: { total: number; awaiting_user: number }
  duration: { avg_ms: number; p95_ms: number; sample: number }
  writer_failures: number
  /** 当前时间窗口的中文标签（后端下发），前端不另算边界 */
  range: string
}

type KindSpec = { label: string | null; color: string; stage: string | null }
interface Contract {
  kinds: Record<string, KindSpec>
  stages: Record<string, { label: string | null; color: string }>
  filter_kinds: string[]
  fallback_kind: KindSpec
  fallback_stage: { label: string | null; color: string }
}

// 桶的中文名与配色。**计数不在这里定义** —— 那来自后端 status_buckets，
// 前端只负责把它翻成人话。桶的成员划分永远只有后端一份（它同时管着过滤）。
const BUCKET_META: Record<string, { label: string; color: string }> = {
  active: { label: '进行中', color: 'oklch(50% 0.1 250)' },
  done: { label: '已完成', color: 'oklch(55% 0.1 155)' },
  failed: { label: '失败', color: 'oklch(50% 0.18 25)' },
  unknown: { label: '未知', color: 'oklch(65% 0.01 70)' },
}
const BUCKET_ORDER = ['active', 'done', 'failed', 'unknown']

const router = useRouter()
const rows = ref<TraceRow[]>([])
const stats = ref<Stats | null>(null)
const contract = ref<Contract | null>(null)
const total = ref(0)
const page = ref(1)
const PAGE_SIZE = 20
const loading = ref(false)
const exporting = ref(false)
const errorMsg = ref('')
const keyword = ref('')
const kindFilter = ref('')
const bucketFilter = ref('')
// 时间范围：'all' | 'today' | '3' | '7' | '30'。默认近 7 天 —— 轨迹页看的是
// 「最近这批需求怎么样」，全时段会把几个月前的历史一起拉进来。
const rangeFilter = ref('7')
// 排序：'created' = 创建时间倒序（默认），'active' = 最近活跃倒序。
// 默认不再是最近活跃：跑着的需求会不断刷新自己的活跃时间、不停顶到第一行，
// 正在看的表会自己重排，观感就是「排序很乱」。
const sortKey = ref<'created' | 'active'>('created')

const maxPage = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

// 时间范围的短标签。KPI「进行中 / 平均完成耗时」跟这个下拉同窗口（否则它们
// 会与状态 chip 对不上），不标出来就成了「同一个数字，含义随下拉静默切换」。
// 直接读后端下发的标签 —— 窗口边界由后端 `_range_since` 唯一解释，
// 前端再拼一份就等于承认有两个真相。
const rangeLabel = computed(() => {
  const r = stats.value?.range
  return !r || r === 'all' ? '' : r
})

// 桶 chip：计数来自后端，顺序固定。count 为 0 的「未知」不显示 ——
// 点进去永远是空列表的 chip 比没有这个 chip 更糟。
const bucketChips = computed(() => {
  const list = stats.value?.status_buckets ?? []
  const byKey = new Map(list.map(b => [b.key, b.count]))
  return BUCKET_ORDER
    .filter(k => k !== 'unknown' || (byKey.get(k) ?? 0) > 0)
    .map(k => ({
      key: k,
      label: BUCKET_META[k]?.label ?? k,
      color: BUCKET_META[k]?.color ?? 'oklch(60% 0.02 70)',
      count: byKey.get(k) ?? 0,
    }))
})

const kindChips = computed(
  () => contract.value?.filter_kinds ?? ['llm_turn', 'tool_call', 'memory'])

const kindSpec = (k: string): KindSpec =>
  contract.value?.kinds[k]
  ?? contract.value?.fallback_kind
  ?? { label: null, color: 'oklch(60% 0.02 70)', stage: null }
const kindColor = (k: string) => kindSpec(k).color
// 契约里没有的类型显示原名 —— 不假装认识它
const kindLabel = (k: string) => kindSpec(k).label ?? k

const deltaText = computed(() => {
  const t = stats.value?.today
  if (!t) return ''
  if (!t.finished_prev) return t.finished > 0 ? '昨日无完成' : '与昨日持平'
  const pct = (t.finished - t.finished_prev) / t.finished_prev * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}% vs 昨日`
})

// 副标题：「最近更新于 N 分钟前」是判断「后台还活着吗」最快的一眼
const subtitle = computed(() => {
  const base = '按需求维度查看 Agent 工作流全过程 · 默认按创建时间倒序'
  const ts = stats.value?.last_event_at
  return ts ? `${base} · 最近更新于 ${relTime(ts)}` : base
})

function fmt(n: number): string {
  // 跨好几个数量级：3.8M 比 3813.5k 好读，而 399.0k 又比 0.4M 精确 ——
  // 所以按量级切档，不写死一个单位
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  return n >= 10000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

function fmtDuration(ms: number): string {
  if (!ms) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

// 创建时间要带年份：跨月/跨年的需求只显示 MM-DD 会分不清是哪一天
function fmtDateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// 相对时间（设计稿的「2 分钟前」）。后端给的是 UTC ISO，Date 会按本地时区解析，
// 所以这里算出来的是真实间隔，不受时区影响。
function relTime(iso: string | null): string {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return '—'
  const diff = Date.now() - t
  if (diff < 60_000) return '刚刚'
  const m = Math.floor(diff / 60_000)
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
}

// 筛选参数只在这里拼一次 —— 列表与导出共用，导出的必定是屏幕上那一份
function buildParams(p: number, size: number): string {
  const params = new URLSearchParams({ page: String(p), page_size: String(size) })
  if (keyword.value.trim()) params.set('q', keyword.value.trim())
  if (kindFilter.value) params.set('kind', kindFilter.value)
  if (bucketFilter.value) params.set('bucket', bucketFilter.value)
  if (rangeFilter.value && rangeFilter.value !== 'all') {
    params.set('range', rangeFilter.value)
  }
  params.set('sort', sortKey.value)
  return params.toString()
}

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await adminFetch<{ items: TraceRow[]; total: number }>(
      `/api/admin/traces?${buildParams(page.value, PAGE_SIZE)}`)
    rows.value = data.items
    total.value = data.total
  } catch (e) {
    errorMsg.value = (e as Error).message || '加载失败'
    rows.value = []
  } finally {
    loading.value = false
  }
}

// 指标与筛选计数只在翻页时不必重拉（go 只调 load），换筛选/换时间范围才重拉。
// **必须带上当前时间范围**：状态 chip 上的计数要和列表行数相等，两边窗口
// 不一致就会变成「写着已完成 17、点进去只有 15」。
async function loadMeta() {
  const qs = rangeFilter.value && rangeFilter.value !== 'all'
    ? `?range=${rangeFilter.value}` : ''
  try {
    stats.value = await adminFetch<Stats>(`/api/admin/traces/stats${qs}`)
  } catch (e) {
    // 指标拉不到不该挡住列表 —— 列表自己能拿到数据就先给列表
    errorMsg.value = errorMsg.value || (e as Error).message || '指标加载失败'
  }
}

async function loadContract() {
  try {
    contract.value = await adminFetch<Contract>('/api/admin/traces/contract')
  } catch {
    // 契约拉不到时 kindChips / kindLabel 有本地兜底，不阻断页面
  }
}

function go(p: number) {
  if (p < 1 || p > maxPage.value) return
  page.value = p
  load()
}

function reload() {
  page.value = 1
  load()
  loadMeta()
}

// 换筛选必然回到第一页：停在第 3 页看一个只有 1 页结果的筛选，只会得到空列表
function pickBucket(b: string) {
  if (bucketFilter.value === b) return
  bucketFilter.value = b
  reload()
}

function pickKind(k: string) {
  if (kindFilter.value === k) return
  kindFilter.value = k
  reload()
}

// 换排序必然回到第一页：停在第 3 页换一种顺序，看到的仍是一批不相干的行
function pickSort(s: 'created' | 'active') {
  if (sortKey.value === s) return
  sortKey.value = s
  reload()
}

function open(id: number) {
  router.push(`/admin/traces/${id}`)
}

// ---- 导出 CSV ----
// 导出的是「当前筛选下的全部」，不是当前这一页 —— 只导 20 行等于让人自己
// 把剩下的页拼起来。仍然沿用 buildParams，所以导出范围和屏幕上看到的一致。
function csvCell(v: unknown): string {
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

async function exportCsv() {
  exporting.value = true
  errorMsg.value = ''
  try {
    const all: TraceRow[] = []
    // 每页 100（接口上限），最多 50 页 —— 兜底防呆循环，不是业务上限
    for (let p = 1; p <= 50; p += 1) {
      const d = await adminFetch<{ items: TraceRow[]; total: number }>(
        `/api/admin/traces?${buildParams(p, 100)}`)
      all.push(...d.items)
      if (!d.items.length || all.length >= d.total) break
    }

    // 成本列在表头声明单位、单元格保持纯数值：带上 ¥ 前缀 Excel 会当文本，就没法求和了
    const header = ['需求编号', '标题', '创建人', '状态', '对话轮次', '事件数',
      'LLM 调用', '工具调用', 'Token', '成本(元)', '耗时(秒)',
      '创建时间', '开始时间', '最近活跃']
    const lines = [header.map(csvCell).join(',')]
    for (const r of all) {
      lines.push([
        r.requirement_id, r.title, r.creator, statusLabel(r.status), r.turn_count,
        r.event_count, r.llm_calls, r.tool_calls, r.tokens, r.cost.toFixed(6),
        Math.round(r.duration_ms / 1000), r.created_at ?? '',
        r.started_at ?? '', r.last_event_at ?? '',
      ].map(csvCell).join(','))
    }

    // BOM：不带的话 Excel 打开中文列名会变乱码
    const blob = new Blob(['\ufeff' + lines.join('\r\n')],
      { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const stamp = new Date().toISOString().slice(0, 10)
    a.href = url
    a.download = `需求轨迹-${stamp}.csv`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch (e) {
    errorMsg.value = (e as Error).message || '导出失败'
  } finally {
    exporting.value = false
  }
}

onMounted(() => {
  load()
  loadMeta()
  loadContract()
})
</script>

<style scoped>
/* ===== 指标卡（与 AdminMetricsView 同一套骨架）===== */
/* min 取 150px 而不是 216px：5 张卡要在常见宽度下排成**一行**。
   auto-fit 会自动折叠空列，所以卡片数少于栏位数时不会留空洞。 */
.kpi-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.kpi-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.kpi-card.alert { border-color: color-mix(in oklab, var(--color-danger) 45%, var(--border)); }
.kpi-head {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
}
.kpi-tile {
  width: 34px; height: 34px; border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
}
.kpi-tile.today { background: var(--color-success-soft); color: var(--color-success); }
.kpi-tile.running { background: color-mix(in oklab, var(--color-info) 14%, transparent); color: var(--color-info); }
.kpi-tile.duration { background: color-mix(in oklab, var(--color-role-agent) 14%, transparent); color: var(--color-role-agent); }
.kpi-tile.cost { background: var(--color-warning-soft); color: var(--color-warning); }
.kpi-tile.cache { background: color-mix(in oklab, var(--color-info) 14%, transparent); color: var(--color-info); }
.kpi-tile.health { background: var(--color-success-soft); color: var(--color-success); }
.kpi-tile.health.bad { background: var(--color-danger-soft); color: var(--color-danger); }
.kpi-chip {
  font-size: 11px; font-weight: 500; padding: 2px 8px; border-radius: 999px;
}
.kpi-chip.ok { background: var(--color-success-soft); color: var(--color-success); }
.kpi-chip.warn { background: var(--color-danger-soft); color: var(--color-danger); }
.kpi-chip.flat { background: var(--accent-soft); color: var(--muted); }
.kpi-label { font-size: 12.5px; color: var(--muted); }
/* 计算窗口标注：只在选了具体时间范围时出现，提示这个数字不是全时段的量 */
.kpi-scope {
  font-style: normal; font-size: 10.5px; color: var(--faint);
  margin-left: 6px; padding: 1px 5px; border-radius: 999px;
  background: var(--chip-bg, rgba(127, 127, 127, 0.12));
  white-space: nowrap;
}
.kpi-value {
  font-size: 26px; font-weight: 600; color: var(--fg); line-height: 1.15;
  font-variant-numeric: tabular-nums;
}
.kpi-sub { font-size: 11.5px; color: var(--faint); line-height: 1.5; }
.dot-warn {
  display: inline-block; width: 5px; height: 5px; border-radius: 50%;
  background: var(--color-warning); margin-right: 5px; vertical-align: middle;
}

/* ===== 筛选条 ===== */
.filterbar {
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  padding: 11px 14px; margin-bottom: 12px;
  display: flex; flex-direction: column; gap: 9px;
}
.frow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.flabel {
  font-size: 11.5px; color: var(--faint); flex-shrink: 0;
  width: 56px; padding-top: 5px;
}
.fchips { display: flex; flex-wrap: wrap; gap: 6px; min-width: 0; }
/* 搜索与时间范围推到右端，与设计稿一致 */
.fright { display: flex; align-items: center; gap: 8px; margin-left: auto; flex-wrap: wrap; }
.fchip {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 4px 10px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--border); background: transparent;
  font-size: 12px; font-family: inherit; color: var(--muted);
  transition: background .12s, border-color .12s, color .12s;
}
.fchip:hover { background: var(--accent-soft); color: var(--accent); }
.fchip.on {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff; font-weight: 600;
}
.fchip b { font-weight: 600; font-variant-numeric: tabular-nums; opacity: .8; }
.fdot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }

.search-box, .range-box {
  display: flex; align-items: center; gap: 7px; padding: 7px 11px;
  background: var(--surface); border: 1px solid var(--border); border-radius: 9px;
  color: var(--faint);
}
.search-box { width: 240px; }
.search-box input, .range-box select {
  border: none; outline: none; font-size: 13px; width: 100%;
  font-family: inherit; color: var(--fg); background: transparent;
}
.range-box select { cursor: pointer; width: auto; padding-right: 2px; }

.notice-bar {
  font-size: 12px; color: var(--muted); background: var(--color-warning-soft);
  border: 1px solid color-mix(in oklab, var(--color-warning) 28%, transparent);
  border-radius: 9px; padding: 8px 12px; margin-bottom: 12px; line-height: 1.6;
}
.notice-bar code {
  background: color-mix(in oklab, var(--color-warning) 18%, transparent);
  padding: 1px 5px; border-radius: 4px; font-size: 11px;
}
.panel {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; overflow: hidden;
}
.trace-table { width: 100%; border-collapse: collapse; font-size: 13px; }
/* 列多（9 列），窄窗口放不下时给表格一个横向滚动容器。
   被裁掉的「操作」列在视觉上等于不存在 —— 用户不会知道还能点进详情。 */
.table-scroll { overflow-x: auto; }
.trace-table th {
  text-align: left; font-weight: 600; font-size: 11.5px; letter-spacing: .04em;
  color: var(--faint); text-transform: uppercase;
  padding: 11px 14px; border-bottom: 1px solid var(--border);
  background: color-mix(in oklab, var(--accent-soft) 40%, transparent);
}
/* nowrap：列宽只够一个字的时候，「失败」会被挤成竖排两行，看着像排版事故 */
.trace-table td {
  padding: 11px 14px; border-bottom: 1px solid var(--border);
  color: var(--fg); white-space: nowrap;
}
.trace-table tbody tr:last-child td { border-bottom: none; }
/* 末列贴边太紧时「查看轨迹 →」的箭头会被压出视野，给最后一列留出内边距 */
.trace-table th:last-child, .trace-table td:last-child { padding-right: 20px; }
/* 可点排序的表头：默认「创建时间」生效，箭头只标在当前生效的那一列上 */
.trace-table th.sortable { cursor: pointer; user-select: none; }
.trace-table th.sortable:hover { color: var(--accent); }
.trace-table th.sortable.on { color: var(--accent); }
.arrow { font-style: normal; margin-left: 3px; }
.c { text-align: center; }
.r { text-align: right; }
.mono { font-variant-numeric: tabular-nums; font-family: var(--font-mono); font-size: 12px; }
.dim { color: var(--muted); }
.muted { color: var(--faint); }
.row-link { cursor: pointer; transition: background .12s; }
.row-link:hover { background: var(--accent-soft); }
/* 标题与元信息两行：需求名要占满，轮次标签别和它抢位置 */
.title-cell { display: flex; flex-direction: column; gap: 4px; max-width: 420px; }
.req-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.req-meta { display: flex; align-items: center; gap: 7px; }
.req-id { color: var(--faint); font-size: 11.5px; font-variant-numeric: tabular-nums; }
.status-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 5px; }
.chip {
  display: inline-block; padding: 2px 7px; border-radius: 5px;
  font-size: 11px; font-weight: 500; white-space: nowrap;
}
.chip-initial { background: color-mix(in oklab, var(--color-info) 14%, transparent); color: var(--color-info); }
.chip-dialog { background: color-mix(in oklab, var(--color-role-agent) 16%, transparent); color: var(--color-role-agent); }
.open-link { font-size: 12px; color: var(--accent); white-space: nowrap; }
.pager {
  display: flex; align-items: center; justify-content: center; gap: 14px;
  padding: 12px; border-top: 1px solid var(--border);
}
.pager button {
  padding: 6px 14px; border: 1px solid var(--border); border-radius: 7px;
  background: var(--surface); font-size: 12.5px; cursor: pointer; font-family: inherit;
  color: var(--fg);
}
.pager button:disabled { opacity: .4; cursor: not-allowed; }
.page-info { font-size: 12.5px; color: var(--muted); }
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
  padding: 44px; text-align: center; color: var(--muted); font-size: 13px;
}
.ghost-btn, .refresh-btn {
  display: inline-flex; align-items: center; gap: 5px; padding: 6px 12px;
  border: 1px solid var(--border); border-radius: 8px; background: var(--surface);
  font-size: 12.5px; cursor: pointer; font-family: inherit; color: var(--fg);
}
.ghost-btn:disabled, .refresh-btn:disabled { opacity: .5; cursor: not-allowed; }

/* ===== 骨架 ===== */
.skeleton-card { padding: 16px; }
.sk-tile { width: 34px; height: 34px; border-radius: 10px; background: var(--accent-soft); margin-bottom: 10px; }
.sk-row { display: flex; gap: 18px; padding: 11px 4px; }
.sk-line { height: 11px; border-radius: 5px; background: var(--accent-soft); }
.sk-line.big { height: 20px; }
.w10 { width: 10%; } .w15 { width: 15%; } .w40 { width: 40%; }
.w50 { width: 50%; } .w60 { width: 60%; } .w70 { width: 70%; }
</style>
