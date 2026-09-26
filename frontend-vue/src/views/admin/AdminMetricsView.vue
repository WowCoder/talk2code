<template>
  <div class="admin-page">
    <header class="admin-header">
      <div>
        <h2 class="admin-page-title">指标看板</h2>
        <p class="admin-page-sub">运营 + 技术指标（数据即时聚合，无缓存）</p>
      </div>
      <div class="header-actions">
        <RouterLink to="/admin/invites" class="nav-link">邀请码审批</RouterLink>
        <button class="nav-link as-btn" @click="logout">退出后台</button>
      </div>
    </header>

    <div v-if="errorMsg" class="error-bar">{{ errorMsg }}</div>

    <template v-if="m">
      <section class="cards">
        <div class="card">
          <span class="card-num">{{ m.users.total }}</span>
          <span class="card-label">用户总数</span>
          <span class="card-sub">今日 +{{ m.users.new_today }} · 7 日 +{{ m.users.new_7d }}</span>
        </div>
        <div class="card">
          <span class="card-num">{{ m.users.active_7d }}</span>
          <span class="card-label">7 日活跃用户</span>
          <span class="card-sub">有需求创建行为</span>
        </div>
        <div class="card">
          <span class="card-num">{{ pct(m.requirements.completion_rate) }}</span>
          <span class="card-label">需求完成率</span>
          <span class="card-sub">finished / (finished + failed)</span>
        </div>
        <div class="card">
          <span class="card-num">{{ m.publish.market_listed }}</span>
          <span class="card-label">市集在售</span>
          <span class="card-sub">发布站点 {{ m.publish.published_total }} 个</span>
        </div>
        <div class="card">
          <span class="card-num">{{ m.invites.pending }}</span>
          <span class="card-label">邀请码待审批</span>
          <span class="card-sub">注册转化 {{ pct(m.invites.conversion) }}</span>
        </div>
        <div class="card">
          <span class="card-num">{{ m.observability.traces_7d }}</span>
          <span class="card-label">7 日 Agent 链路</span>
          <span class="card-sub">均耗时 {{ (m.observability.avg_duration_ms / 1000).toFixed(1) }}s · 成本 {{ m.observability.cost_7d }}</span>
        </div>
      </section>

      <section class="panel-row">
        <div class="panel">
          <h3 class="panel-title">需求状态分布</h3>
          <ul class="dist-list">
            <li v-for="(count, status) in m.requirements.status" :key="status">
              <span class="dist-key">{{ status }}</span>
              <span class="dist-val">{{ count }}</span>
            </li>
          </ul>
          <div v-if="m.requirements.top_failures.length" class="failures">
            <h4 class="panel-sub">失败原因 Top</h4>
            <ul class="dist-list">
              <li v-for="f in m.requirements.top_failures" :key="f.reason">
                <span class="dist-key" :title="f.reason">{{ f.reason }}</span>
                <span class="dist-val">{{ f.count }}</span>
              </li>
            </ul>
          </div>
        </div>

        <div class="panel">
          <h3 class="panel-title">需求热度榜 Top {{ m.top_heat.length }}</h3>
          <p class="panel-sub">与市集前台同一公式（compute_heat），数字保证一致</p>
          <ol class="heat-list">
            <li v-for="h in m.top_heat" :key="h.slug">
              <span class="heat-score">{{ h.heat }}</span>
              <span class="heat-title" :title="h.title">{{ h.title || h.slug }}</span>
              <span class="heat-meta">{{ h.views }} 看 · {{ h.likes }} 赞 · {{ h.comments }} 言</span>
            </li>
            <li v-if="!m.top_heat.length" class="empty">暂无上架作品</li>
          </ol>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { adminFetch, clearAdminSession } from '@/composables/useAdmin'

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

const router = useRouter()
const m = ref<Metrics | null>(null)
const errorMsg = ref('')

onMounted(async () => {
  try {
    m.value = await adminFetch<Metrics>('/api/admin/metrics')
  } catch (err: any) {
    errorMsg.value = err.message
    if (err.message.includes('登录已过期')) router.push('/admin/login')
  }
})

function pct(v: number): string {
  return `${Math.round(v * 100)}%`
}

function logout() {
  clearAdminSession()
  router.push('/admin/login')
}
</script>

<style scoped>
.admin-page {
  min-height: 100vh;
  background: var(--bg);
  padding: 28px 24px 60px;
  max-width: 1080px;
  margin: 0 auto;
}

.admin-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 18px;
}

.admin-page-title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 700;
  color: var(--fg);
}

.admin-page-sub {
  font-size: 13px;
  color: var(--muted);
  margin-top: 4px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.nav-link {
  font-size: 13px;
  color: var(--accent);
  text-decoration: none;
}

.nav-link.as-btn {
  border: none;
  background: transparent;
  cursor: pointer;
  font-family: var(--font-body);
  padding: 0;
}

.error-bar {
  margin-bottom: 12px;
  padding: 10px 14px;
  border: 1px solid oklch(60% 0.15 20);
  border-radius: 10px;
  background: oklch(96% 0.01 20);
  color: oklch(50% 0.15 20);
  font-size: 13px;
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.card-num {
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 700;
  color: var(--fg);
}

.card-label {
  font-size: 13px;
  color: var(--fg);
}

.card-sub {
  font-size: 11px;
  color: var(--muted);
}

.panel-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

@media (max-width: 800px) {
  .panel-row { grid-template-columns: 1fr; }
}

.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
}

.panel-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--fg);
  margin-bottom: 8px;
}

.panel-sub {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 8px;
}

.dist-list {
  list-style: none;
}

.dist-list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}

.dist-list li:last-child { border-bottom: none; }

.dist-key {
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 280px;
}

.dist-val {
  color: var(--muted);
  font-family: var(--font-mono, monospace);
}

.failures { margin-top: 14px; }

.heat-list {
  list-style: none;
  counter-reset: heat;
}

.heat-list li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}

.heat-list li:last-child { border-bottom: none; }

.heat-score {
  min-width: 34px;
  text-align: center;
  font-family: var(--font-mono, monospace);
  font-weight: 500;
  color: var(--accent);
}

.heat-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg);
}

.heat-meta {
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
}

.empty {
  color: var(--muted);
  justify-content: center;
}
</style>
