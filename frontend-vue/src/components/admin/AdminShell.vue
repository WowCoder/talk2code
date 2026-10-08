<template>
  <div class="admin-shell">
    <aside class="admin-sidebar">
      <div class="brand-row">
        <img src="@/assets/logo.png" alt="Talk2Code" class="brand-logo" />
        <span class="brand-name">Talk<span class="brand-accent">2</span>Code</span>
        <span class="brand-tag">后台</span>
      </div>

      <p class="nav-group">运营</p>

      <RouterLink to="/admin/metrics" class="nav-item" active-class="active">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="3" y="3" width="7.5" height="7.5" rx="2" />
          <rect x="13.5" y="3" width="7.5" height="7.5" rx="2" />
          <rect x="3" y="13.5" width="7.5" height="7.5" rx="2" />
          <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2" />
        </svg>
        <span class="nav-label">总览</span>
      </RouterLink>

      <RouterLink to="/admin/invites" class="nav-item" active-class="active">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round">
          <path d="M20.6 13.4 12 4.8H5v7l8.6 8.6a2 2 0 0 0 2.8 0l4.2-4.2a2 2 0 0 0 0-2.8Z" />
          <circle cx="9" cy="9" r="1.4" fill="currentColor" stroke="none" />
        </svg>
        <span class="nav-label">邀请码审批</span>
        <span v-if="pending > 0" class="nav-badge">{{ pending > 99 ? '99+' : pending }}</span>
      </RouterLink>

      <RouterLink to="/admin/traces" class="nav-item" active-class="active">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M3 12h4l2.5-6 4 12L16 12h5" />
        </svg>
        <span class="nav-label">需求轨迹</span>
      </RouterLink>

      <p class="nav-group">研发</p>

      <!-- 评测集：离线跑评测留下的过程与结果。与需求轨迹共用同一套渲染，
           但数据源不同（本地 SQLite vs 运营库），所以单独成组，不混进「运营」。 -->
      <RouterLink to="/admin/evals" class="nav-item" active-class="active">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="5" y="4" width="14" height="17" rx="2.5" />
          <path d="M9 3.5h6v2.5H9z" />
          <path d="m8.5 13.5 2.2 2.2 4.3-4.3" />
          <path d="M8.5 18.5h7" />
        </svg>
        <span class="nav-label">评测集</span>
      </RouterLink>

      <p class="nav-group">规划中</p>

      <!-- 后端目前只有登录 / 邀请码 / 指标三类接口，未落地的模块按「规划中」置灰，
           而不是画一个点了没反应的入口 -->
      <span class="nav-item disabled" title="后端暂无对应接口">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round">
          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" />
          <path d="M14 3v5h5" />
        </svg>
        <span class="nav-label">需求管理</span>
      </span>
      <span class="nav-item disabled" title="后端暂无对应接口">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <circle cx="9" cy="8" r="3.5" />
          <path d="M3.5 20c0-3 2.5-5 5.5-5s5.5 2 5.5 5" />
          <circle cx="17" cy="9" r="2.5" />
          <path d="M16.5 15.5c2.3.4 4 2 4 4.5" />
        </svg>
        <span class="nav-label">用户管理</span>
      </span>
      <span class="nav-item disabled" title="后端暂无对应接口">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="8.5" />
          <path d="M3.5 12h17" />
          <path d="M12 3.5c2.5 2.3 3.8 5.2 3.8 8.5s-1.3 6.2-3.8 8.5c-2.5-2.3-3.8-5.2-3.8-8.5s1.3-6.2 3.8-8.5Z" />
        </svg>
        <span class="nav-label">发布站点</span>
      </span>

      <div class="sidebar-foot">
        <RouterLink to="/" class="nav-item">
          <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 4h6v6M20 4l-9 9" />
            <path d="M18 13.5V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5.5" />
          </svg>
          <span class="nav-label">返回前台</span>
        </RouterLink>

        <div class="admin-card">
          <span class="admin-avatar">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <circle cx="12" cy="8" r="4" />
              <path d="M4.5 20c1.5-3.2 4.3-5 7.5-5s6 1.8 7.5 5" />
            </svg>
          </span>
          <span class="admin-meta">
            <span class="admin-name">{{ username || 'admin' }}</span>
            <span class="admin-sub">管理员</span>
          </span>
          <button class="logout-btn" title="退出后台" @click="logout">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M9 21H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3" />
              <path d="M16 17l5-5-5-5M21 12H9" />
            </svg>
          </button>
        </div>
      </div>
    </aside>

    <main class="admin-main">
      <header class="admin-topbar">
        <div class="topbar-left">
          <h1 class="page-title">{{ title }}</h1>
          <p v-if="subtitle" class="page-sub">{{ subtitle }}</p>
        </div>
        <div class="topbar-right">
          <slot name="actions" />
        </div>
      </header>
      <div class="admin-body">
        <slot />
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { clearAdminSession, getAdminUsername } from '@/composables/useAdmin'

withDefaults(defineProps<{
  title: string
  subtitle?: string
  /** 待审批数量，用于侧栏角标。传 -1 表示未知（不显示角标）。 */
  pending?: number
}>(), {
  subtitle: '',
  pending: -1,
})

const router = useRouter()
const username = getAdminUsername() || ''

function logout() {
  clearAdminSession()
  router.push('/admin/login')
}
</script>

<style scoped>
.admin-shell {
  display: flex;
  min-height: 100vh;
  background: var(--bg);
}

/* ===== 侧边栏 ===== */
.admin-sidebar {
  width: 232px;
  flex-shrink: 0;
  height: 100vh;
  position: sticky;
  top: 0;
  background: var(--surface);
  border-right: 1px solid var(--border);
  padding: 20px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  margin-bottom: 12px;
}

.brand-logo {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  flex-shrink: 0;
}

.brand-name {
  font-family: var(--font-display);
  font-size: 17px;
  font-weight: 700;
  letter-spacing: -0.03em;
  color: var(--fg);
  white-space: nowrap;
}

.brand-accent {
  color: var(--accent);
}

.brand-tag {
  padding: 2px 5px;
  border-radius: 6px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 10px;
  font-weight: 600;
  flex-shrink: 0;
}

.nav-group {
  font-size: 11px;
  color: var(--muted);
  padding: 8px;
  margin-top: 8px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 40px;
  padding: 0 12px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 500;
  color: var(--fg);
  transition: background 0.15s, color 0.15s;
}

.nav-item:hover {
  background: var(--bg);
}

.nav-item.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}

.nav-item.disabled {
  color: var(--muted);
  opacity: 0.45;
  cursor: not-allowed;
}

.nav-item.disabled:hover {
  background: none;
}

.nav-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.nav-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.nav-badge {
  margin-left: auto;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--accent);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  font-family: var(--font-body);
  display: flex;
  align-items: center;
  justify-content: center;
}

.sidebar-foot {
  margin-top: auto;
  padding-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.admin-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border-radius: 12px;
  background: var(--bg);
}

.admin-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.admin-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.admin-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.admin-sub {
  font-size: 10px;
  color: var(--muted);
}

.logout-btn {
  border: none;
  background: none;
  color: var(--muted);
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  transition: color 0.15s, background 0.15s;
}

.logout-btn:hover {
  color: var(--accent);
  background: var(--surface);
}

/* ===== 主区 ===== */
.admin-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.admin-topbar {
  min-height: 72px;
  padding: 16px 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  flex-shrink: 0;
}

.page-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--fg);
}

.page-sub {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.admin-body {
  padding: 8px 32px 48px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ===== 窄屏：侧栏收成图标栏 ===== */
@media (max-width: 900px) {
  .admin-sidebar {
    width: 68px;
    padding: 16px 10px;
  }

  .brand-name,
  .brand-tag,
  .nav-group,
  .admin-meta,
  .nav-badge {
    display: none;
  }

  .nav-item {
    justify-content: center;
    padding: 0;
  }

  .admin-card {
    justify-content: center;
  }

  .admin-topbar,
  .admin-body {
    padding-left: 16px;
    padding-right: 16px;
  }
}
</style>
