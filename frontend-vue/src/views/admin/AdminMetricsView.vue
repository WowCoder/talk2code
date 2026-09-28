<template>
  <AdminShell title="总览" subtitle="数据实时聚合 · 每 15 秒自动刷新" :pending="m?.invites.pending ?? -1">
    <template #actions>
      <span class="update-chip">
        <span class="live-dot"></span>
        {{ updatedAt ? `更新于 ${updatedAt}` : '加载中…' }}
      </span>
      <button class="refresh-btn" :disabled="loading" @click="reload">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" />
        </svg>
        {{ loading ? '刷新中' : '刷新' }}
      </button>
    </template>

    <div v-if="errorMsg" class="error-bar">{{ errorMsg }}</div>

    <!-- 首屏骨架：指标聚合要扫全表，先给结构再给数字，避免整页跳动 -->
    <template v-if="!m && !errorMsg">
      <div class="kpi-row">
        <div v-for="i in 4" :key="i" class="kpi-card skeleton-card">
          <div class="sk-tile"></div>
          <div class="sk-line w60"></div>
          <div class="sk-line w40 big"></div>
          <div class="sk-line w80"></div>
        </div>
      </div>
      <div class="row">
        <div class="panel skeleton-card"><div class="sk-line w40 big"></div><div class="sk-line w90"></div><div class="sk-line w70"></div></div>
        <div class="panel skeleton-card"><div class="sk-line w40 big"></div><div class="sk-line w90"></div><div class="sk-line w70"></div></div>
      </div>
    </template>

    <template v-else-if="m">
      <!-- ===== 关键指标 ===== -->
      <div class="kpi-row">
        <div class="kpi-card">
          <div class="kpi-head">
            <span class="kpi-tile users">
              <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                <circle cx="9" cy="8" r="3.5" />
                <path d="M3.5 20c0-3 2.5-5 5.5-5s5.5 2 5.5 5" />
                <circle cx="17" cy="9" r="2.5" />
                <path d="M16.5 15.5c2.3.4 4 2 4 4.5" />
              </svg>
            </span>
            <span class="kpi-chip up">今日 +{{ m.users.new_today }}</span>
          </div>
          <span class="kpi-label">用户总数</span>
          <span class="kpi-value">{{ fmt(m.users.total) }}</span>
          <span class="kpi-sub">今日 +{{ m.users.new_today }} · 7 日 +{{ m.users.new_7d }}</span>
        </div>

        <div class="kpi-card">
          <div class="kpi-head">
            <span class="kpi-tile activity">
              <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M3 12h4l2.5-6 4 12L16 12h5" />
              </svg>
            </span>
            <span class="kpi-chip">7 日需求 +{{ m.requirements.new_7d }}</span>
          </div>
          <span class="kpi-label">7 日活跃用户</span>
          <span class="kpi-value">{{ fmt(m.users.active_7d) }}</span>
          <span class="kpi-sub">有需求创建行为的用户</span>
        </div>

        <div class="kpi-card">
          <div class="kpi-head">
            <span class="kpi-tile rate">
              <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="8.5" />
                <path d="M8.5 12.2l2.4 2.4 4.6-5" />
              </svg>
            </span>
            <span class="kpi-chip warn">失败 {{ m.requirements.status.failed || 0 }}</span>
          </div>
          <span class="kpi-label">需求完成率</span>
          <span class="kpi-value">{{ pct(m.requirements.completion_rate) }}</span>
          <span class="kpi-sub mono">finished / (finished + failed)</span>
        </div>

        <div class="kpi-card">
          <div class="kpi-head">
            <span class="kpi-tile market">
              <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round">
                <path d="M4 8h16l-1.2 11a2 2 0 0 1-2 1.8H7.2a2 2 0 0 1-2-1.8Z" />
                <path d="M8.5 11V7a3.5 3.5 0 0 1 7 0v4" stroke-linecap="round" />
              </svg>
            </span>
            <span class="kpi-chip">站点 {{ m.publish.published_total }}</span>
          </div>
          <span class="kpi-label">市集在售</span>
          <span class="kpi-value">{{ fmt(m.publish.market_listed) }}</span>
          <span class="kpi-sub">发布站点共 {{ m.publish.published_total }} 个</span>
        </div>
      </div>

      <!-- ===== 待办 + 链路观测 ===== -->
      <div class="row">
        <div class="action-card">
          <div class="action-left">
            <span class="action-value">{{ m.invites.pending }}</span>
            <span class="action-label">邀请码待审批</span>
            <span class="action-sub">注册转化 {{ pct(m.invites.conversion) }} · 处理后自动发码</span>
          </div>
          <RouterLink to="/admin/invites" class="action-btn">
            去审批
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
          </RouterLink>
        </div>

        <div class="panel observe-panel">
          <div class="panel-head">
            <h3 class="panel-title">Agent 链路观测</h3>
            <span class="panel-chip">近 7 日</span>
          </div>
          <div class="stat-row">
            <div class="stat">
              <span class="stat-value">{{ fmt(m.observability.traces_7d) }}</span>
              <span class="stat-label">链路总数</span>
            </div>
            <span class="v-divider"></span>
            <div class="stat">
              <span class="stat-value">{{ seconds(m.observability.avg_duration_ms) }}</span>
              <span class="stat-label">平均耗时</span>
            </div>
            <span class="v-divider"></span>
            <div class="stat">
              <span class="stat-value">¥{{ m.observability.cost_7d.toFixed(2) }}</span>
              <span class="stat-label">7 日成本</span>
            </div>
          </div>
        </div>
      </div>

      <!-- ===== 需求分布 + 热度榜 ===== -->
      <div class="row">
        <div class="panel">
          <div class="panel-head">
            <h3 class="panel-title">需求状态分布</h3>
            <span class="panel-total mono">共 {{ fmt(m.requirements.total) }} 条</span>
          </div>

            <div v-for="row in statusRows" :key="row.key" class="bar-row">
              <span class="bar-label">{{ row.label }}</span>
              <span class="bar-track">
                <span class="bar-fill" :style="{ width: barWidth(row.count, maxStatus), background: row.color }"></span>
              </span>
            <span class="bar-count mono">{{ row.count }}</span>
          </div>
          <p v-if="!statusRows.length" class="inline-empty">暂无需求数据</p>

          <template v-if="m.requirements.top_failures.length">
            <div class="panel-divider"></div>
            <h4 class="panel-subtitle">失败原因 Top {{ m.requirements.top_failures.length }}</h4>
            <div v-for="f in m.requirements.top_failures" :key="f.reason" class="bar-row">
              <span class="bar-label wide" :title="f.reason">{{ f.reason }}</span>
              <span class="bar-track">
                <span class="bar-fill" :style="{ width: barWidth(f.count, maxFailure), background: 'oklch(70% 0.09 28)' }"></span>
              </span>
              <span class="bar-count mono">{{ f.count }}</span>
            </div>
          </template>
        </div>

        <div class="panel">
          <div class="panel-head">
            <h3 class="panel-title">需求热度榜</h3>
            <span class="panel-chip accent">Top 5</span>
          </div>
          <p class="panel-desc">与市集前台同一公式计算，数字保持一致</p>

          <div v-for="(h, i) in topHeat" :key="h.slug" class="heat-row">
            <span class="heat-rank" :class="{ first: i === 0 }">{{ i + 1 }}</span>
            <span class="heat-main">
              <span class="heat-title" :title="h.title || h.slug">{{ h.title || h.slug }}</span>
              <span class="heat-meta">{{ compact(h.views) }} 看 · {{ h.likes }} 赞 · {{ h.comments }} 言</span>
            </span>
            <span class="heat-score mono">{{ h.heat }}</span>
          </div>
          <p v-if="!topHeat.length" class="inline-empty">暂无上架作品</p>
        </div>
      </div>

      <!-- ===== 邀请码分布 + 市集互动 ===== -->
      <div class="row">
        <div class="panel">
          <div class="panel-head">
            <h3 class="panel-title">邀请码状态分布</h3>
            <span class="panel-total mono">共 {{ inviteTotal }} 个</span>
          </div>
          <div class="stack-bar">
            <span
              v-for="seg in inviteSegments"
              :key="seg.key"
              class="stack-seg"
              :style="{ flexGrow: seg.count, background: seg.color }"
              :title="`${seg.label} ${seg.count}`"
            ></span>
          </div>
          <div v-if="!inviteSegments.length" class="inline-empty">暂无邀请码数据</div>
          <div class="legend">
            <div v-for="seg in inviteSegments" :key="seg.key" class="legend-item">
              <span class="legend-dot" :style="{ background: seg.color }"></span>
              <span class="legend-label">{{ seg.label }}</span>
              <span class="legend-count mono">{{ seg.count }}</span>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <h3 class="panel-title">市集互动</h3>
            <span class="panel-chip">累计</span>
          </div>
          <div class="metric-row">
            <div class="metric">
              <span class="metric-tile views">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
                  <circle cx="12" cy="12" r="3" />
                </svg>
              </span>
              <span class="metric-body">
                <span class="metric-value">{{ compact(m.publish.total_views) }}</span>
                <span class="metric-label">总浏览</span>
              </span>
            </div>
            <div class="metric">
              <span class="metric-tile likes">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round">
                  <path d="M7 10v11H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h3Zm0 0 4.2-7.4a1.8 1.8 0 0 1 3.3 1L13.5 8H19a2 2 0 0 1 2 2.4l-1.6 8A2 2 0 0 1 17.4 20H7" />
                </svg>
              </span>
              <span class="metric-body">
                <span class="metric-value">{{ fmt(m.publish.total_likes) }}</span>
                <span class="metric-label">总点赞</span>
              </span>
            </div>
            <div class="metric">
              <span class="metric-tile comments">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round">
                  <path d="M21 12a8 8 0 0 1-8 8H4l2.3-2.9A8 8 0 1 1 21 12Z" />
                </svg>
              </span>
              <span class="metric-body">
                <span class="metric-value">{{ fmt(m.publish.total_comments) }}</span>
                <span class="metric-label">总评论</span>
              </span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </AdminShell>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AdminShell from '@/components/admin/AdminShell.vue'
import { adminFetch } from '@/composables/useAdmin'

interface Metrics {
  users: { total: number; new_today: number; new_7d: number; active_7d: number }
  requirements: {
    total: number; new_today: number; new_7d: number
    status: Record<string, number>
    completion_rate: number
    top_failures: { reason: string; count: number }[]
  }
  publish: { published_total: number; market_listed: number; total_views: number; total_likes: number; total_comments: number }
  invites: { distribution: Record<string, number>; pending: number; issued: number; used: number; conversion: number }
  top_heat: { requirement_id: number | null; slug: string; title: string; heat: number; heat_raw: number; views: number; likes: number; comments: number }[]
  observability: { traces_7d: number; avg_duration_ms: number; cost_7d: number }
}

// 状态命名沿用主站 components/common/StatusBadge.vue —— 同一个状态在后台叫
// 另一个名字，等于逼人做一次心算翻译。
const REQ_STATUS: Record<string, { label: string; color: string }> = {
  finished: { label: '已完成', color: 'oklch(55% 0.1 155)' },
  finished_with_issues: { label: '已完成 (有问题)', color: 'oklch(62% 0.13 65)' },
  needs_user_input: { label: '待用户处理', color: 'oklch(65% 0.12 85)' },
  processing: { label: '处理中', color: 'oklch(50% 0.1 250)' },
  planning: { label: '待确认', color: 'oklch(62% 0.09 230)' },
  pending: { label: '等待中', color: 'oklch(72% 0.1 85)' },
  interrupted: { label: '已中断', color: 'oklch(62% 0.01 70)' },
  failed: { label: '失败', color: 'oklch(50% 0.18 25)' },
  unknown: { label: '未知', color: 'oklch(65% 0.01 70)' },
}

const INVITE_STATUS: Record<string, { label: string; color: string }> = {
  used: { label: '已使用', color: 'oklch(55% 0.1 155)' },
  issued: { label: '已发放', color: 'oklch(50% 0.1 250)' },
  pending: { label: '待审批', color: 'oklch(65% 0.12 85)' },
  rejected: { label: '已拒绝', color: 'oklch(65% 0.01 70)' },
  revoked: { label: '已吊销', color: 'oklch(50% 0.18 25)' },
  expired: { label: '已过期', color: 'oklch(60% 0.06 40)' },
}

const router = useRouter()
const m = ref<Metrics | null>(null)
const errorMsg = ref('')
const loading = ref(false)
const updatedAt = ref('')
let timer: number | undefined

const statusRows = computed(() => {
  if (!m.value) return []
  const dist = m.value.requirements.status || {}
  return Object.entries(dist)
    .map(([key, count]) => ({
      key,
      label: REQ_STATUS[key]?.label || key,
      color: REQ_STATUS[key]?.color || REQ_STATUS.unknown.color,
      count,
    }))
    .sort((a, b) => b.count - a.count)
})

const inviteSegments = computed(() => {
  if (!m.value) return []
  const dist = m.value.invites.distribution || {}
  return Object.entries(dist)
    .filter(([, count]) => count > 0)
    .map(([key, count]) => ({
      key,
      label: INVITE_STATUS[key]?.label || key,
      color: INVITE_STATUS[key]?.color || REQ_STATUS.unknown.color,
      count,
    }))
    .sort((a, b) => b.count - a.count)
})

const inviteTotal = computed(() => inviteSegments.value.reduce((sum, s) => sum + s.count, 0))
const topHeat = computed(() => (m.value?.top_heat || []).slice(0, 5))

const maxStatus = computed(() => Math.max(1, ...statusRows.value.map((r) => r.count)))
const maxFailure = computed(() =>
  Math.max(1, ...(m.value?.requirements.top_failures || []).map((f) => f.count)),
)

/** 条形图按「本组最大值」归一化：两组基数差两个数量级，共用一把尺子会让失败原因全变成一根线。 */
function barWidth(count: number, max: number): string {
  return `${Math.max(2, Math.round((count / Math.max(1, max)) * 100))}%`
}

onMounted(() => {
  reload()
  timer = window.setInterval(() => {
    if (document.visibilityState === 'visible') reload()
  }, 15000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})

async function reload() {
  if (loading.value) return
  loading.value = true
  try {
    m.value = await adminFetch<Metrics>('/api/admin/metrics')
    updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    errorMsg.value = ''
  } catch (err: any) {
    errorMsg.value = err.message
    if (err.message.includes('登录已过期')) router.push('/admin/login')
  } finally {
    loading.value = false
  }
}

function fmt(n: number): string {
  return (n ?? 0).toLocaleString('en-US')
}

function compact(n: number): string {
  const v = n ?? 0
  if (v >= 10000) return `${(v / 10000).toFixed(1)}w`
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`
  return String(v)
}

function pct(v: number): string {
  return `${Math.round((v ?? 0) * 100)}%`
}

function seconds(ms: number): string {
  return `${((ms ?? 0) / 1000).toFixed(1)}s`
}
</script>

<style scoped>
.mono {
  font-family: var(--font-mono);
}

.error-bar {
  padding: 10px 14px;
  border: 1px solid oklch(60% 0.15 20);
  border-radius: 10px;
  background: oklch(96% 0.01 20);
  color: oklch(50% 0.15 20);
  font-size: 13px;
}

/* ===== 顶栏操作 ===== */
.update-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}

.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-success);
}

.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--fg);
  font-size: 13px;
  font-family: var(--font-body);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}

.refresh-btn:hover:not(:disabled) {
  background: var(--bg);
  border-color: var(--accent);
  color: var(--accent);
}

.refresh-btn:disabled {
  opacity: 0.55;
  cursor: wait;
}

/* ===== 布局 ===== */
.kpi-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 16px;
}

.row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

@media (max-width: 1080px) {
  .row {
    grid-template-columns: 1fr;
  }
}

.kpi-card,
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 18px;
}

.kpi-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.kpi-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.kpi-tile {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.kpi-tile.users { background: var(--accent-soft); color: var(--accent); }
.kpi-tile.activity { background: oklch(50% 0.1 250 / 12%); color: var(--color-info); }
.kpi-tile.rate { background: oklch(55% 0.1 155 / 12%); color: var(--color-success); }
.kpi-tile.market { background: oklch(65% 0.12 85 / 16%); color: var(--color-warning); }

.kpi-chip {
  padding: 3px 8px;
  border-radius: 6px;
  background: oklch(55% 0.1 155 / 12%);
  color: var(--color-success);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.kpi-chip.up { background: var(--accent-soft); color: var(--accent); }
.kpi-chip.warn { background: oklch(65% 0.12 85 / 16%); color: oklch(50% 0.12 85); }

.kpi-label {
  font-size: 13px;
  color: var(--muted);
}

.kpi-value {
  font-family: var(--font-display);
  font-size: 30px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--fg);
  line-height: 1.1;
}

.kpi-sub {
  font-size: 12px;
  color: var(--muted);
}

/* ===== 待办行动卡 ===== */
.action-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px;
  border-radius: 14px;
  border: 1px solid var(--accent-soft);
  background: oklch(88% 0.04 38 / 45%);
}

.action-left {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.action-value {
  font-family: var(--font-display);
  font-size: 30px;
  font-weight: 700;
  line-height: 1.1;
  color: var(--accent);
}

.action-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--fg);
}

.action-sub {
  font-size: 12px;
  color: var(--accent);
  opacity: 0.75;
}

.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 11px 16px;
  border-radius: 10px;
  background: var(--accent);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  transition: background 0.15s;
}

.action-btn:hover {
  background: oklch(58% 0.13 28);
}

/* ===== 通用面板 ===== */
.panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
}

.panel-chip,
.panel-total {
  padding: 3px 8px;
  border-radius: 6px;
  background: var(--bg);
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
}

.panel-chip.accent {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}

.panel-desc {
  font-size: 12px;
  color: var(--muted);
  margin-top: -6px;
}

.panel-subtitle {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
}

.panel-divider {
  height: 1px;
  background: var(--border);
  margin: 2px 0;
}

.inline-empty {
  font-size: 13px;
  color: var(--muted);
  text-align: center;
  padding: 16px 0;
}

/* ===== 条形图 ===== */
.bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.bar-label {
  width: 92px;
  flex-shrink: 0;
  font-size: 13px;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bar-label.wide {
  width: 120px;
}

.bar-track {
  flex: 1;
  height: 8px;
  border-radius: 4px;
  background: var(--bg);
  overflow: hidden;
}

.bar-fill {
  display: block;
  height: 8px;
  border-radius: 4px;
  transition: width 0.4s ease;
}

.bar-count {
  width: 44px;
  flex-shrink: 0;
  text-align: right;
  font-size: 12px;
  color: var(--muted);
}

/* ===== 热度榜 ===== */
.heat-row {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 46px;
  padding: 0 4px;
  border-radius: 8px;
  transition: background 0.15s;
}

.heat-row:hover {
  background: var(--bg);
}

.heat-rank {
  width: 24px;
  height: 24px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}

.heat-rank.first {
  background: var(--accent);
  color: #fff;
}

.heat-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.heat-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.heat-meta {
  font-size: 11px;
  color: var(--muted);
}

.heat-score {
  font-size: 14px;
  font-weight: 500;
  color: var(--accent);
}

/* ===== 堆叠条 + 图例 ===== */
.stack-bar {
  display: flex;
  height: 12px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--bg);
  gap: 2px;
}

.stack-seg {
  min-width: 4px;
  border-radius: 3px;
}

.legend {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 8px 12px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.legend-label {
  flex: 1;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.legend-count {
  font-size: 12px;
  color: var(--muted);
}

/* ===== 观测面板 ===== */
.observe-panel {
  justify-content: center;
}

.stat-row {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-value {
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--fg);
}

.stat-label {
  font-size: 12px;
  color: var(--muted);
}

.v-divider {
  width: 1px;
  height: 32px;
  background: var(--border);
}

/* ===== 市集互动 ===== */
.metric-row {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}

.metric {
  display: flex;
  align-items: center;
  gap: 12px;
}

.metric-tile {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.metric-tile.views { background: var(--accent-soft); color: var(--accent); }
.metric-tile.likes { background: oklch(50% 0.1 250 / 12%); color: var(--color-info); }
.metric-tile.comments { background: oklch(55% 0.1 155 / 12%); color: var(--color-success); }

.metric-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.metric-value {
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--fg);
}

.metric-label {
  font-size: 12px;
  color: var(--muted);
}

/* ===== 骨架屏 ===== */
.skeleton-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sk-tile {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: var(--bg);
}

.sk-line {
  height: 12px;
  border-radius: 6px;
  background: var(--bg);
}

.sk-line.big {
  height: 26px;
}

.sk-line.w40 { width: 40%; }
.sk-line.w60 { width: 60%; }
.sk-line.w70 { width: 70%; }
.sk-line.w80 { width: 80%; }
.sk-line.w90 { width: 90%; }

.skeleton-card .sk-line,
.skeleton-card .sk-tile {
  animation: pulse 1.6s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
</style>
