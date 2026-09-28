<template>
  <AdminShell
    title="邀请码审批"
    :subtitle="`待处理 ${pendingCount} 条 · 每 15 秒自动刷新`"
    :pending="pendingCount"
  >
    <template #actions>
      <span class="update-chip">
        <span class="live-dot"></span>
        {{ updatedAt ? `更新于 ${updatedAt}` : '加载中…' }}
      </span>
      <button class="refresh-btn" :disabled="loading" @click="reload">刷新</button>
    </template>

    <div v-if="errorMsg" class="error-bar">{{ errorMsg }}</div>

    <div class="toolbar">
      <div class="segment">
        <button
          v-for="t in TABS"
          :key="t.value"
          class="seg-btn"
          :class="{ active: statusFilter === t.value }"
          @click="pickTab(t.value)"
        >
          {{ t.label }}<span v-if="counts[t.value] !== undefined" class="seg-count">{{ counts[t.value] }}</span>
        </button>
      </div>

      <div class="search-box">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <circle cx="11" cy="11" r="7" />
          <path d="M16.5 16.5 21 21" />
        </svg>
        <input v-model="keyword" type="search" placeholder="搜索邮箱 / 邀请码" @input="onSearchInput" />
      </div>
    </div>

    <div class="table-card">
      <div class="table-head">
        <span>申请人</span>
        <span>用途说明</span>
        <span>申请时间</span>
        <span>状态</span>
        <span>邀请码 / 投递</span>
        <span class="ops-head">操作</span>
      </div>

      <div v-if="!items.length && !loading" class="empty-state">
        <svg viewBox="0 0 24 24" width="44" height="44" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round">
          <path d="M4 7h16l-1.2 11a2 2 0 0 1-2 1.8H7.2a2 2 0 0 1-2-1.8Z" />
          <path d="M8.5 10.5V7a3.5 3.5 0 0 1 7 0v3.5" />
        </svg>
        <p class="empty-title">{{ keyword ? '没有匹配的申请' : `暂无${statusLabel(statusFilter) || ''}申请` }}</p>
        <p class="empty-sub">有新的注册申请时会自动出现在这里</p>
      </div>

      <div
        v-for="row in items"
        :key="row.id"
        class="table-row"
        :class="{ pending: row.status === 'pending' }"
        @click="openDetail(row)"
      >
        <span class="cell applicant">
          <span class="email">{{ row.applicant_email }}</span>
          <span class="phone mono">{{ row.applicant_phone || '—' }}</span>
        </span>
        <span class="cell note" :title="row.applicant_note">{{ row.applicant_note || '—' }}</span>
        <span class="cell mono time">{{ shortTime(row.created_at) }}</span>
        <span class="cell">
          <span class="badge" :class="row.status">{{ statusLabel(row.status) }}</span>
        </span>
        <span class="cell code-cell mono">
          <span class="code">{{ row.code || '—' }}</span>
          <span v-if="row.code" class="delivery" :class="row.delivery_status">{{ deliveryLabel(row.delivery_status) }}</span>
        </span>
        <span class="cell ops" @click.stop>
          <template v-if="row.status === 'pending'">
            <button class="op approve" :disabled="busy === row.id" @click="approve(row)">通过</button>
            <button class="op reject" :disabled="busy === row.id" @click="openReject(row)">拒绝</button>
          </template>
          <template v-else-if="row.status === 'issued'">
            <button v-if="row.delivery_status !== 'sent'" class="op resend" :disabled="busy === row.id" @click="resend(row)">重发邮件</button>
            <button class="op reject" :disabled="busy === row.id" @click="openRevoke(row)">吊销</button>
          </template>
          <span v-else class="op-none">—</span>
        </span>
      </div>
    </div>

    <div class="pager">
      <span class="pager-total">共 {{ total }} 条 · 每页 {{ PAGE_SIZE }} 条 · 第 {{ page }} 页</span>
      <div class="pager-actions">
        <button class="page-btn" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
        <button class="page-btn primary" :disabled="page * PAGE_SIZE >= total" @click="goPage(page + 1)">下一页</button>
      </div>
    </div>
  </AdminShell>

  <!-- ===== 申请详情抽屉：替代 window.confirm / prompt ===== -->
  <Teleport to="body">
    <div v-if="detail" class="drawer-mask" @click="closeDetail">
      <aside class="drawer" role="dialog" aria-label="申请详情" @click.stop>
        <header class="drawer-head">
          <h2 class="drawer-title">申请详情</h2>
          <button class="close-btn" aria-label="关闭" @click="closeDetail">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </header>

        <div class="drawer-body">
          <div class="info-card">
            <div class="info-row"><span class="info-label">邮箱</span><span class="info-value">{{ detail.applicant_email }}</span></div>
            <div class="info-row"><span class="info-label">手机号</span><span class="info-value mono">{{ detail.applicant_phone || '—' }}</span></div>
            <div class="info-row"><span class="info-label">申请时间</span><span class="info-value mono">{{ fullTime(detail.created_at) }}</span></div>
            <div class="info-row">
              <span class="info-label">状态</span>
              <span class="badge" :class="detail.status">{{ statusLabel(detail.status) }}</span>
            </div>
            <div v-if="detail.reject_reason" class="info-row">
              <span class="info-label">拒绝理由</span><span class="info-value">{{ detail.reject_reason }}</span>
            </div>
          </div>

          <section class="drawer-section">
            <h3 class="section-title">用途说明</h3>
            <div class="note-card">{{ detail.applicant_note || '申请人未填写' }}</div>
          </section>

          <section class="drawer-section">
            <h3 class="section-title">审批进度</h3>
            <div class="step">
              <span class="step-node done">
                <svg viewBox="0 0 16 16" width="16" height="16"><circle cx="8" cy="8" r="8" fill="currentColor" /><path d="M4.6 8.2l2.2 2.2 4.4-4.6" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" /></svg>
              </span>
              <span class="step-text">已提交申请</span>
              <span class="step-time mono">{{ shortTime(detail.created_at) }}</span>
            </div>

            <div class="step">
              <span class="step-node" :class="step2State">
                <template v-if="step2State === 'done'">
                  <svg viewBox="0 0 16 16" width="16" height="16"><circle cx="8" cy="8" r="8" fill="currentColor" /><path d="M4.6 8.2l2.2 2.2 4.4-4.6" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" /></svg>
                </template>
                <template v-else-if="step2State === 'failed'">
                  <svg viewBox="0 0 16 16" width="16" height="16"><circle cx="8" cy="8" r="8" fill="currentColor" /><path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="#fff" stroke-width="1.8" stroke-linecap="round" /></svg>
                </template>
                <template v-else>
                  <svg viewBox="0 0 16 16" width="16" height="16"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="2" /><circle cx="8" cy="8" r="3" fill="currentColor" /></svg>
                </template>
              </span>
              <span class="step-text">{{ step2Text }}</span>
              <span class="step-time mono">{{ step2Time }}</span>
            </div>

            <div class="step">
              <span class="step-node" :class="step3State">
                <template v-if="step3State === 'done'">
                  <svg viewBox="0 0 16 16" width="16" height="16"><circle cx="8" cy="8" r="8" fill="currentColor" /><path d="M4.6 8.2l2.2 2.2 4.4-4.6" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" /></svg>
                </template>
                <template v-else>
                  <svg viewBox="0 0 16 16" width="16" height="16"><circle cx="8" cy="8" r="7" fill="var(--bg)" stroke="var(--border)" stroke-width="1.5" /></svg>
                </template>
              </span>
              <span class="step-text">发放邀请码并邮件通知</span>
              <span class="step-time mono">{{ step3Time }}</span>
            </div>
          </section>
        </div>

        <footer v-if="detail.status === 'pending' || detail.status === 'issued'" class="drawer-foot">
          <template v-if="detail.status === 'pending'">
            <label class="reason-label" for="reject-reason">拒绝理由（通过邮件发送给申请人，可留空）</label>
            <input
              id="reject-reason"
              v-model="rejectReason"
              class="input-field reason-input"
              placeholder="例：当前名额有限，欢迎下期再申请"
            />
            <div class="foot-actions">
              <button class="foot-btn danger" :disabled="busy === detail.id" @click="confirmReject">拒绝</button>
              <button class="foot-btn primary" :disabled="busy === detail.id" @click="approve(detail)">通过并发送邀请码</button>
            </div>
          </template>
          <template v-else>
            <div class="foot-actions">
              <button
                v-if="detail.delivery_status !== 'sent'"
                class="foot-btn ghost"
                :disabled="busy === detail.id"
                @click="resend(detail)"
              >重发邮件</button>
              <button class="foot-btn danger" :disabled="busy === detail.id" @click="openRevoke(detail)">吊销邀请码</button>
            </div>
          </template>
        </footer>
      </aside>
    </div>

    <!-- ===== 二次确认：不可逆操作 ===== -->
    <div v-if="confirmState" class="modal-mask" @click="confirmState = null">
      <div class="modal" role="dialog" aria-modal="true" @click.stop>
        <h3 class="modal-title">{{ confirmState.title }}</h3>
        <p class="modal-desc">{{ confirmState.desc }}</p>
        <div class="modal-actions">
          <button class="modal-btn cancel" @click="confirmState = null">取消</button>
          <button class="modal-btn confirm" :class="{ danger: confirmState.danger }" @click="runConfirm">
            {{ confirmState.confirmText }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AdminShell from '@/components/admin/AdminShell.vue'
import { adminFetch, type InviteRow } from '@/composables/useAdmin'

const TABS = [
  { value: '', label: '全部' },
  { value: 'pending', label: '待审批' },
  { value: 'issued', label: '已发放' },
  { value: 'used', label: '已使用' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'revoked', label: '已吊销' },
] as const

const PAGE_SIZE = 50

const router = useRouter()
const items = ref<InviteRow[]>([])
const total = ref(0)
const page = ref(1)
const pendingCount = ref(0)
const statusFilter = ref('')
const keyword = ref('')
const errorMsg = ref('')
const loading = ref(false)
const busy = ref<number | null>(null)
const updatedAt = ref('')
const detail = ref<InviteRow | null>(null)
const rejectReason = ref('')
const confirmState = ref<{
  title: string
  desc: string
  confirmText: string
  danger: boolean
  action: () => Promise<void>
} | null>(null)

/** 各状态计数：切过的 Tab 才有数（后端只回当前筛选的 total），避免为填角标多发 5 个请求。 */
const counts = reactive<Record<string, number>>({})

let timer: number | undefined
let searchTimer: number | undefined

onMounted(() => {
  reload()
  timer = window.setInterval(() => {
    if (document.visibilityState === 'visible' && !detail.value) reload()
  }, 15000)
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
  if (searchTimer) window.clearTimeout(searchTimer)
  window.removeEventListener('keydown', onKeydown)
})

function onKeydown(e: KeyboardEvent) {
  if (e.key !== 'Escape') return
  if (confirmState.value) confirmState.value = null
  else if (detail.value) closeDetail()
}

async function reload() {
  if (loading.value) return
  loading.value = true
  try {
    const q = new URLSearchParams({ page: String(page.value), page_size: String(PAGE_SIZE) })
    if (statusFilter.value) q.set('status', statusFilter.value)
    if (keyword.value.trim()) q.set('keyword', keyword.value.trim())
    const data = await adminFetch<{ items: InviteRow[]; total: number; page: number; pending: number }>(
      `/api/admin/invites?${q}`,
    )
    items.value = data.items
    total.value = data.total
    pendingCount.value = data.pending
    counts[statusFilter.value] = data.total
    counts.pending = data.pending
    // 抽屉里可能是旧对象：用新数据同步，避免后台刷新把详情内容抽走
    if (detail.value) {
      const fresh = data.items.find((r) => r.id === detail.value!.id)
      if (fresh) detail.value = fresh
    }
    updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    errorMsg.value = ''
  } catch (err: any) {
    errorMsg.value = err.message
    if (err.message.includes('登录已过期')) router.push('/admin/login')
  } finally {
    loading.value = false
  }
}

function pickTab(value: string) {
  if (statusFilter.value === value) return
  statusFilter.value = value
  page.value = 1
  reload()
}

function goPage(next: number) {
  if (next < 1) return
  page.value = next
  reload()
}

function onSearchInput() {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    page.value = 1
    reload()
  }, 350)
}

function openDetail(row: InviteRow) {
  detail.value = row
  rejectReason.value = ''
}

function closeDetail() {
  detail.value = null
  rejectReason.value = ''
}

function openReject(row: InviteRow) {
  openDetail(row)
  confirmState.value = {
    title: '确认拒绝该申请？',
    desc: '拒绝后会向申请人发送邮件（含你填写的理由），此操作不可撤销。',
    confirmText: '确认拒绝',
    danger: true,
    action: () => reject(row),
  }
}

function confirmReject() {
  if (!detail.value) return
  openReject(detail.value)
}

function openRevoke(row: InviteRow) {
  confirmState.value = {
    title: `确认吊销 ${row.code || '该邀请码'}？`,
    desc: '吊销后这个码将无法用于注册，且不能恢复。',
    confirmText: '确认吊销',
    danger: true,
    action: () => revoke(row),
  }
}

async function runConfirm() {
  const cfg = confirmState.value
  if (!cfg) return
  confirmState.value = null
  await cfg.action()
}

async function approve(row: InviteRow) {
  busy.value = row.id
  try {
    await adminFetch(`/api/admin/invites/${row.id}/approve`, { method: 'POST' })
    closeDetail()
    await reload()
  } catch (err: any) {
    errorMsg.value = err.message
  } finally {
    busy.value = null
  }
}

async function reject(row: InviteRow) {
  busy.value = row.id
  try {
    await adminFetch(`/api/admin/invites/${row.id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason: rejectReason.value.trim() }),
    })
    closeDetail()
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
    if (detail.value?.id === row.id) detail.value = { ...detail.value, delivery_status: data.delivery_status }
  } catch (err: any) {
    errorMsg.value = err.message
  } finally {
    busy.value = null
  }
}

async function revoke(row: InviteRow) {
  busy.value = row.id
  try {
    await adminFetch(`/api/admin/invites/${row.id}/revoke`, { method: 'POST' })
    if (detail.value?.id === row.id) closeDetail()
    await reload()
  } catch (err: any) {
    errorMsg.value = err.message
  } finally {
    busy.value = null
  }
}

/* ===== 审批进度时间线 ===== */
const step2State = computed(() => {
  const s = detail.value?.status
  if (s === 'pending') return 'current'
  if (s === 'rejected') return 'failed'
  return 'done'
})

const step2Text = computed(() => {
  const s = detail.value?.status
  if (s === 'pending') return '等待审批'
  if (s === 'rejected') return '已拒绝'
  return '审批通过'
})

const step2Time = computed(() => {
  const d = detail.value
  if (!d) return ''
  if (d.status === 'pending') return `已等待 ${waited(d.created_at)}`
  return shortTime(d.decided_at)
})

const step3State = computed(() => (detail.value && detail.value.status !== 'pending' && detail.value.status !== 'rejected' ? 'done' : 'todo'))
const step3Time = computed(() => {
  const d = detail.value
  if (!d) return ''
  if (d.status === 'pending' || d.status === 'rejected') return '—'
  return d.code || '—'
})

/** 后端序列化的时间是 UTC 却不带时区标记，直接 new Date() 会被按本地时区解析，
 *  出现「申请时间 16:26、已等待 8 小时」这类自相矛盾的展示。 */
function toDate(iso: string | null): Date | null {
  if (!iso) return null
  const normalized = /[zZ]|[-+]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`
  const d = new Date(normalized)
  return Number.isNaN(d.getTime()) ? null : d
}

function fmtLocal(d: Date, withYear = false): string {
  const p = (n: number) => String(n).padStart(2, '0')
  const md = `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
  return withYear ? `${d.getFullYear()}-${md}` : md
}

function waited(iso: string | null): string {
  const d = toDate(iso)
  if (!d) return '—'
  const ms = Date.now() - d.getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  const hours = Math.floor(ms / 3600000)
  if (hours >= 24) return `${Math.floor(hours / 24)} 天`
  if (hours >= 1) return `${hours} 小时`
  return `${Math.max(1, Math.floor(ms / 60000))} 分钟`
}

function shortTime(iso: string | null): string {
  const d = toDate(iso)
  return d ? fmtLocal(d) : '—'
}

function fullTime(iso: string | null): string {
  const d = toDate(iso)
  return d ? fmtLocal(d, true) : '—'
}

function statusLabel(s: string): string {
  return ({
    pending: '待审批', issued: '已发放', used: '已使用',
    rejected: '已拒绝', revoked: '已吊销', expired: '已过期',
  } as Record<string, string>)[s] || s
}

function deliveryLabel(d: string): string {
  return ({ pending: '待发送', sent: '邮件已发送', failed: '邮件发送失败', skipped: '未发信' } as Record<string, string>)[d] || ''
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
  padding: 9px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--fg);
  font-size: 13px;
  font-family: var(--font-body);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
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

/* ===== 工具栏 ===== */
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.segment {
  display: flex;
  gap: 2px;
  padding: 4px;
  border-radius: 10px;
  background: var(--bg);
  flex-wrap: wrap;
}

.seg-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 12px;
  border: none;
  border-radius: 8px;
  background: none;
  color: var(--muted);
  font-size: 13px;
  font-family: var(--font-body);
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s;
}

.seg-btn:hover {
  color: var(--fg);
}

.seg-btn.active {
  background: var(--surface);
  color: var(--accent);
  font-weight: 600;
  box-shadow: 0 1px 3px oklch(45% 0.06 40 / 12%);
}

.seg-count {
  font-size: 11px;
  opacity: 0.7;
}

.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 240px;
  max-width: 100%;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--muted);
}

.search-box input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: none;
  font-size: 13px;
  font-family: var(--font-body);
  color: var(--fg);
}

.search-box input::placeholder {
  color: var(--muted);
}

/* ===== 表格 ===== */
.table-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
}

.table-head,
.table-row {
  display: grid;
  grid-template-columns: 240px 1fr 130px 96px 170px 150px;
  align-items: center;
  gap: 12px;
  padding: 0 16px;
}

.table-head {
  height: 40px;
  background: var(--bg);
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
}

.ops-head {
  text-align: right;
}

.table-row {
  min-height: 56px;
  border-top: 1px solid var(--border);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
}

.table-row:hover {
  background: var(--bg);
}

.table-row.pending {
  background: oklch(88% 0.04 38 / 22%);
}

.table-row.pending:hover {
  background: oklch(88% 0.04 38 / 40%);
}

.cell {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.cell.time {
  font-size: 12px;
  color: var(--muted);
}

.applicant .email {
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.applicant .phone {
  font-size: 12px;
  color: var(--muted);
}

.note {
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}

.code-cell .code {
  color: var(--fg);
}

.delivery {
  font-size: 11px;
  font-family: var(--font-body);
}

.delivery.sent { color: var(--color-success); }
.delivery.failed { color: var(--color-danger); }
.delivery.pending, .delivery.skipped { color: var(--muted); }

.badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

.badge.pending { background: oklch(65% 0.12 85 / 18%); color: oklch(48% 0.12 85); }
.badge.issued { background: oklch(50% 0.1 250 / 12%); color: var(--color-info); }
.badge.used { background: oklch(55% 0.1 155 / 14%); color: var(--color-success); }
.badge.rejected { background: var(--bg); color: var(--muted); }
.badge.revoked, .badge.expired { background: oklch(50% 0.18 25 / 12%); color: var(--color-danger); }

.ops {
  flex-direction: row;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}

.op {
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--fg);
  font-size: 12px;
  font-weight: 500;
  font-family: var(--font-body);
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}

.op.approve { border-color: var(--accent); color: var(--accent); }
.op.approve:hover:not(:disabled) { background: var(--accent); color: #fff; }
.op.resend:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.op.reject { border-color: oklch(80% 0.08 25); color: var(--color-danger); }
.op.reject:hover:not(:disabled) { background: var(--color-danger); border-color: var(--color-danger); color: #fff; }
.op:disabled { opacity: 0.5; cursor: wait; }

.op-none {
  color: var(--muted);
  text-align: right;
  display: block;
  width: 100%;
}

/* ===== 空状态 ===== */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 48px 16px;
  color: var(--border);
}

.empty-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
}

.empty-sub {
  font-size: 12px;
  color: var(--muted);
}

/* ===== 分页 ===== */
.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.pager-total {
  font-size: 12px;
  color: var(--muted);
}

.pager-actions {
  display: flex;
  gap: 8px;
}

.page-btn {
  padding: 7px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--muted);
  font-size: 12px;
  font-family: var(--font-body);
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}

.page-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}

.page-btn.primary {
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 500;
}

.page-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ===== 抽屉 ===== */
.drawer-mask {
  position: fixed;
  inset: 0;
  background: oklch(22% 0.02 50 / 45%);
  z-index: 200;
  display: flex;
  justify-content: flex-end;
}

.drawer {
  width: 480px;
  max-width: 100%;
  height: 100%;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  box-shadow: -8px 0 32px oklch(10% 0.02 50 / 22%);
  animation: slide-in 0.22s ease;
}

@keyframes slide-in {
  from { transform: translateX(24px); opacity: 0.4; }
  to { transform: translateX(0); opacity: 1; }
}

.drawer-head {
  height: 64px;
  padding: 0 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.drawer-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--fg);
}

.close-btn {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: var(--bg);
  color: var(--muted);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
}

.close-btn:hover {
  color: var(--fg);
  background: var(--border);
}

.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.info-card {
  background: var(--bg);
  border-radius: 12px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.info-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.info-label {
  width: 64px;
  flex-shrink: 0;
  color: var(--muted);
  font-size: 12px;
}

.info-value {
  color: var(--fg);
  word-break: break-all;
}

.drawer-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--fg);
}

.note-card {
  background: var(--bg);
  border-radius: 12px;
  padding: 14px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--fg);
  white-space: pre-wrap;
  word-break: break-word;
}

.step {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.step-node {
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.step-node.done { color: var(--color-success); }
.step-node.current { color: var(--accent); }
.step-node.failed { color: var(--color-danger); }
.step-node.todo { color: var(--border); }

.step-text {
  color: var(--fg);
}

.step-time {
  margin-left: auto;
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
}

.drawer-foot {
  border-top: 1px solid var(--border);
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex-shrink: 0;
}

.reason-label {
  font-size: 12px;
  color: var(--muted);
}

.reason-input {
  background: var(--bg);
}

.foot-actions {
  display: flex;
  gap: 10px;
}

.foot-btn {
  flex: 1;
  height: 44px;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-body);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, opacity 0.15s;
}

.foot-btn.primary {
  border: none;
  background: var(--accent);
  color: #fff;
}

.foot-btn.primary:hover:not(:disabled) {
  background: oklch(58% 0.13 28);
}

.foot-btn.danger {
  border-color: oklch(80% 0.08 25);
  color: var(--color-danger);
}

.foot-btn.danger:hover:not(:disabled) {
  background: var(--color-danger);
  border-color: var(--color-danger);
  color: #fff;
}

.foot-btn.ghost:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}

.foot-btn:disabled {
  opacity: 0.5;
  cursor: wait;
}

/* ===== 二次确认弹窗 ===== */
.modal-mask {
  position: fixed;
  inset: 0;
  background: oklch(22% 0.02 50 / 45%);
  z-index: 300;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.modal {
  width: 380px;
  max-width: 100%;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  box-shadow: 0 16px 48px oklch(10% 0.02 50 / 20%);
}

.modal-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--fg);
}

.modal-desc {
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted);
}

.modal-actions {
  display: flex;
  gap: 10px;
  margin-top: 6px;
}

.modal-btn {
  flex: 1;
  height: 38px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--fg);
  font-size: 13px;
  font-weight: 500;
  font-family: var(--font-body);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}

.modal-btn.cancel:hover {
  background: var(--bg);
}

.modal-btn.confirm {
  border: none;
  background: var(--accent);
  color: #fff;
  font-weight: 600;
}

.modal-btn.confirm.danger {
  background: var(--color-danger);
}

.modal-btn.confirm:hover {
  filter: brightness(0.95);
}

@media (max-width: 1080px) {
  .table-card {
    overflow-x: auto;
  }

  .table-head,
  .table-row {
    min-width: 960px;
  }
}
</style>
