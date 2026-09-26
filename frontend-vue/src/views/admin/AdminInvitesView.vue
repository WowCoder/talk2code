<template>
  <div class="admin-page">
    <header class="admin-header">
      <div>
        <h2 class="admin-page-title">邀请码审批</h2>
        <p class="admin-page-sub">
          待处理 <strong>{{ pendingCount }}</strong> 条 · 每 15 秒自动刷新
        </p>
      </div>
      <div class="header-actions">
        <select v-model="statusFilter" class="filter-select" @change="reload">
          <option value="pending">待审批</option>
          <option value="issued">已发放</option>
          <option value="used">已使用</option>
          <option value="rejected">已拒绝</option>
          <option value="revoked">已吊销</option>
          <option value="">全部</option>
        </select>
        <RouterLink to="/admin/metrics" class="nav-link">指标看板</RouterLink>
        <button class="nav-link as-btn" @click="logout">退出后台</button>
      </div>
    </header>

    <div v-if="errorMsg" class="error-bar">{{ errorMsg }}</div>

    <table class="invite-table">
      <thead>
        <tr>
          <th>申请时间</th>
          <th>邮箱</th>
          <th>手机号</th>
          <th>用途</th>
          <th>状态</th>
          <th>邀请码 / 发送</th>
          <th class="ops-col">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in items" :key="row.id">
          <td class="mono">{{ shortTime(row.created_at) }}</td>
          <td>{{ row.applicant_email }}</td>
          <td class="mono">{{ row.applicant_phone }}</td>
          <td class="note" :title="row.applicant_note">{{ row.applicant_note || '—' }}</td>
          <td><span class="badge" :class="row.status">{{ statusLabel(row.status) }}</span></td>
          <td class="mono">
            <template v-if="row.code">{{ row.code }}</template>
            <template v-else>—</template>
            <span v-if="row.code" class="delivery" :class="row.delivery_status">{{ deliveryLabel(row.delivery_status) }}</span>
          </td>
          <td class="ops-col">
            <template v-if="row.status === 'pending'">
              <button class="op approve" :disabled="busy === row.id" @click="approve(row)">通过</button>
              <button class="op reject" :disabled="busy === row.id" @click="reject(row)">拒绝</button>
            </template>
            <template v-else-if="row.status === 'issued'">
              <button
                v-if="row.delivery_status !== 'sent'"
                class="op"
                :disabled="busy === row.id"
                @click="resend(row)"
              >重发邮件</button>
              <button class="op reject" :disabled="busy === row.id" @click="revoke(row)">吊销</button>
            </template>
            <span v-else class="op-none">—</span>
          </td>
        </tr>
        <tr v-if="!items.length && !errorMsg">
          <td colspan="7" class="empty">暂无{{ statusLabel(statusFilter) }}申请</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { adminFetch, clearAdminSession, type InviteRow } from '@/composables/useAdmin'

const router = useRouter()
const items = ref<InviteRow[]>([])
const pendingCount = ref(0)
const statusFilter = ref('pending')
const errorMsg = ref('')
const busy = ref<number | null>(null)
let timer: number | undefined

onMounted(() => {
  reload()
  // 实时审批：页面可见时 15s 轮询。内测申请量级（每天几十条）下轮询完全无感，
  // SSE 是量级上来之后的事（见设计文档 §5.2）
  timer = window.setInterval(() => {
    if (document.visibilityState === 'visible') reload()
  }, 15000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})

async function reload() {
  try {
    const q = new URLSearchParams({ page: '1', page_size: '50' })
    if (statusFilter.value) q.set('status', statusFilter.value)
    const data = await adminFetch<{ items: InviteRow[]; pending: number }>(`/api/admin/invites?${q}`)
    items.value = data.items
    pendingCount.value = data.pending
    errorMsg.value = ''
  } catch (err: any) {
    errorMsg.value = err.message
    if (err.message.includes('登录已过期')) router.push('/admin/login')
  }
}

async function approve(row: InviteRow) {
  busy.value = row.id
  try {
    const data = await adminFetch<{ delivery_status: string }>(`/api/admin/invites/${row.id}/approve`, { method: 'POST' })
    row.status = 'issued'
    row.delivery_status = data.delivery_status
    await reload()
  } catch (err: any) {
    errorMsg.value = err.message
  } finally {
    busy.value = null
  }
}

async function reject(row: InviteRow) {
  const reason = window.prompt('拒绝理由（会随邮件发给申请人，可留空）：') || ''
  busy.value = row.id
  try {
    await adminFetch(`/api/admin/invites/${row.id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    })
    await reload()
  } catch (err: any) {
    errorMsg.value = err.message
  } finally {
    busy.value = null
  }
}

async function resend(row: InviteRow) {
  busy.value = row.id
  try {
    const data = await adminFetch<{ delivery_status: string }>(`/api/admin/invites/${row.id}/resend`, { method: 'POST' })
    row.delivery_status = data.delivery_status
  } catch (err: any) {
    errorMsg.value = err.message
  } finally {
    busy.value = null
  }
}

async function revoke(row: InviteRow) {
  if (!window.confirm(`确定吊销 ${row.code}？吊销后该码将无法注册。`)) return
  busy.value = row.id
  try {
    await adminFetch(`/api/admin/invites/${row.id}/revoke`, { method: 'POST' })
    await reload()
  } catch (err: any) {
    errorMsg.value = err.message
  } finally {
    busy.value = null
  }
}

function logout() {
  clearAdminSession()
  router.push('/admin/login')
}

function shortTime(iso: string | null): string {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(5, 16)
}

function statusLabel(s: string): string {
  return ({ pending: '待审批', issued: '已发放', used: '已使用', rejected: '已拒绝', revoked: '已吊销', expired: '已过期' } as Record<string, string>)[s] || s
}

function deliveryLabel(d: string): string {
  return ({ pending: '待发送', sent: '已发送', failed: '发送失败', skipped: '未发信' } as Record<string, string>)[d] || d
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
  gap: 12px;
  flex-wrap: wrap;
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

.filter-select {
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--surface);
  color: var(--fg);
  font-size: 13px;
  font-family: var(--font-body);
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

.invite-table {
  width: 100%;
  border-collapse: collapse;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  font-size: 13px;
}

.invite-table th,
.invite-table td {
  padding: 10px 12px;
  text-align: left;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}

.invite-table th {
  background: var(--surface);
  color: var(--muted);
  font-weight: 500;
  font-size: 12px;
}

.invite-table tbody tr:last-child td {
  border-bottom: none;
}

.mono {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  white-space: nowrap;
}

.note {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
}

.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 8px;
  font-size: 12px;
}

.badge.pending { background: #FAEEDA; color: #854F0B; }
.badge.issued { background: #E6F1FB; color: #185FA5; }
.badge.used { background: #EAF3DE; color: #3B6D11; }
.badge.rejected, .badge.revoked { background: #F1EFE8; color: #5F5E5A; }
.badge.expired { background: #FCEBEB; color: #A32D2D; }

.delivery {
  display: block;
  margin-top: 4px;
  font-size: 11px;
}

.delivery.failed { color: #A32D2D; }
.delivery.sent { color: #3B6D11; }
.delivery.skipped, .delivery.pending { color: #888780; }

.ops-col {
  white-space: nowrap;
}

.op {
  padding: 5px 12px;
  margin-right: 6px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--fg);
  font-size: 12px;
  font-family: var(--font-body);
  cursor: pointer;
  transition: all 0.15s;
}

.op.approve {
  border-color: var(--accent);
  color: var(--accent);
}

.op.approve:hover { background: var(--accent); color: #fff; }

.op.reject:hover { border-color: oklch(60% 0.15 20); color: oklch(55% 0.15 20); }

.op:disabled { opacity: 0.5; cursor: wait; }

.op-none { color: var(--muted); }

.empty {
  text-align: center;
  color: var(--muted);
  padding: 28px 0 !important;
}
</style>
