<template>
  <nav class="nav" :class="{ compact }">
    <div class="nav-left">
      <template v-if="compact">
        <button class="nav-back" aria-label="返回历史记录" @click="$router.push('/history')">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M15 19l-7-7 7-7"/>
          </svg>
        </button>
        <span class="nav-title">{{ title }}</span>
      </template>
      <template v-else>
        <router-link :to="isGuest ? '/market' : '/'" class="nav-brand">
          <img src="@/assets/logo.png" alt="Talk2Code" class="nav-logo" /><span class="brand-text">Talk<span>2</span>Code</span>
        </router-link>
        <div class="nav-links">
          <!-- 顶部三个常驻 TAB：首页 / 创意市集 / 历史记录。设置收进右侧头像下拉，
               避免低频入口占据常驻位置。 -->
          <template v-if="isGuest">
            <span class="nav-link disabled" title="登录后可查看">首页</span>
            <router-link to="/market" class="nav-link" active-class="active">
              创意市集
            </router-link>
            <span class="nav-link disabled" title="登录后可查看">历史记录</span>
          </template>
          <template v-else>
            <router-link to="/" class="nav-link" exact-active-class="active">
              首页
            </router-link>
            <router-link to="/market" class="nav-link" active-class="active">
              创意市集
            </router-link>
            <router-link to="/history" class="nav-link" active-class="active">
              历史记录
            </router-link>
          </template>
        </div>
      </template>
    </div>
    <div class="nav-right">
      <router-link v-if="isGuest && !compact" class="nav-guest-link" to="/login">
        登录 / 注册
      </router-link>
      <template v-if="compact && statusText">
        <span class="nav-status" :class="{ running: isActive }">
          <span v-if="isActive" class="status-dot"></span>
          {{ statusText }}
        </span>
      </template>
      <div v-if="!isGuest" class="nav-user" @click="toggleDropdown"
           ref="userRef" role="button" tabindex="0"
           @keydown.enter.prevent="toggleDropdown" @keydown.space.prevent="toggleDropdown">
        <span class="nav-avatar">{{ authStore.username[0]?.toUpperCase() }}</span>
        <span v-if="!compact" class="nav-username">{{ authStore.username }}</span>
        <svg class="nav-caret" :class="{ open: showDropdown }" viewBox="0 0 24 24" width="12" height="12"
             fill="none" stroke="currentColor" stroke-width="2.5"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M6 9l6 6 6-6"/>
        </svg>
      </div>
      <div v-if="showDropdown" class="nav-dropdown">
        <!-- 下拉头部：当前登录身份 -->
        <div class="dropdown-head">
          <span class="dropdown-avatar">{{ authStore.username[0]?.toUpperCase() }}</span>
          <div class="dropdown-id">
            <span class="dropdown-name">{{ authStore.username }}</span>
            <span v-if="authStore.email" class="dropdown-email">{{ authStore.email }}</span>
          </div>
        </div>
        <div class="dropdown-divider"></div>
        <!-- 下拉只留高频个人入口：设置 + 退出登录 -->
        <button class="dropdown-item" :class="{ current: isOnSettings }" @click="goSettings">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="3"/>
            <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/>
          </svg>
          设置
        </button>
        <button class="dropdown-item logout-item" @click="handleLogout">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/>
            <polyline points="16 17 21 12 16 7"/>
            <line x1="21" y1="12" x2="9" y2="12"/>
          </svg>
          退出登录
        </button>
      </div>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useRoute, useRouter } from 'vue-router'

const props = withDefaults(defineProps<{
  compact?: boolean
  title?: string
  statusText?: string
  isActive?: boolean
}>(), {
  compact: false,
  title: '',
  statusText: '',
  isActive: false,
})

const authStore = useAuthStore()
const router = useRouter()
const route = useRoute()

const showDropdown = ref(false)
const userRef = ref<HTMLElement | null>(null)

// 游客态：市集可逛，右上角给登录入口，不渲染用户头像与下拉。
const isGuest = computed(() => !authStore.isAuthenticated)

const isOnSettings = computed(() => route.path.startsWith('/settings'))

function toggleDropdown() {
  showDropdown.value = !showDropdown.value
}

function closeDropdown() {
  showDropdown.value = false
}

function onDocumentClick(e: MouseEvent) {
  if (userRef.value && !userRef.value.contains(e.target as Node)) {
    closeDropdown()
  }
}

function onEscKey(e: KeyboardEvent) {
  if (e.key === 'Escape') closeDropdown()
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
  document.addEventListener('keydown', onEscKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  document.removeEventListener('keydown', onEscKey)
})

function goSettings() {
  closeDropdown()
  router.push('/settings')
}

async function handleLogout() {
  closeDropdown()
  await authStore.logout()
  router.push('/login')
}
</script>

<style scoped>
.nav {
  background: oklch(99% 0.008 70 / 88%);
  background: rgba(255, 251, 246, 0.92);
  border-bottom: 1px solid var(--border);
  padding: 0 24px;
  height: 68px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
  backdrop-filter: blur(12px);
  z-index: 50;
}

.nav.compact {
  height: 56px;
}

.nav-left {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
}

.nav-brand {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  color: var(--fg);
  display: flex;
  align-items: center;
  gap: 8px;
}

.brand-text {
  letter-spacing: -0.04em;
}

.nav-logo {
  width: 32px;
  height: 32px;
  border-radius: 8px;
}

.brand-text span {
  color: var(--accent);
}

.nav-links {
  display: flex;
  gap: 4px;
  margin-left: 24px;
}

.nav-link {
  padding: 7px 16px;
  border-radius: 9px;
  font-size: 13.5px;
  font-weight: 500;
  color: var(--muted);
  transition: color 0.15s, background 0.15s;
  cursor: pointer;
}

.nav-link:hover {
  color: var(--fg);
  background: var(--accent-soft);
}

.nav-link.active {
  color: var(--accent-strong);
  background: var(--accent-soft);
  font-weight: 600;
}

.nav-link.disabled {
  color: var(--muted);
  opacity: 0.45;
  cursor: not-allowed;
}

.nav-link.disabled:hover {
  background: none;
  color: var(--muted);
}

.nav-guest-link {
  padding: 7px 16px;
  border: 1px solid var(--border);
  border-radius: 9px;
  font-size: 13px;
  font-weight: 500;
  color: var(--accent-strong);
  text-decoration: none;
  transition: border-color 0.15s, background 0.15s;
}

.nav-guest-link:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.nav-back {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  cursor: pointer;
  border: 1px solid var(--border);
  background: none;
  transition: color 0.15s, background 0.15s;
  flex-shrink: 0;
}

.nav-back:hover {
  color: var(--fg);
  background: var(--accent-soft);
}

.nav-title {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--fg);
}

.nav-right {
  display: flex;
  align-items: center;
  gap: 12px;
  position: relative;
}

.nav-user {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 9px;
  cursor: pointer;
  transition: background 0.15s;
}

.nav-user:hover,
.nav-user:focus-visible {
  background: var(--accent-soft);
}

.nav-caret {
  color: var(--faint);
  transition: transform 0.2s;
}

.nav-caret.open {
  transform: rotate(180deg);
}

.nav-dropdown {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 10px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 12px 32px rgba(34, 23, 19, 0.14);
  min-width: 224px;
  padding: 6px;
  z-index: 100;
  overflow: hidden;
}

.dropdown-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 10px 12px;
}

.dropdown-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  flex-shrink: 0;
}

.dropdown-id {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.dropdown-name {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--fg);
}

.dropdown-email {
  font-size: 11.5px;
  color: var(--faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dropdown-divider {
  height: 1px;
  background: var(--border);
  margin: 0 6px 4px;
}

.dropdown-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: none;
  border-radius: 8px;
  font-size: 13px;
  font-family: var(--font-body);
  color: var(--fg);
  cursor: pointer;
  transition: background 0.15s;
}

.dropdown-item:hover {
  background: var(--accent-soft);
}

.dropdown-item.current {
  color: var(--accent-strong);
  background: var(--accent-soft);
  font-weight: 600;
}

.logout-item:hover {
  color: var(--color-danger);
  background: var(--color-danger-soft);
}

.nav-status {
  font-size: 12px;
  color: var(--muted);
  display: inline-flex;
  align-items: center;
  gap: 2px;
  max-width: 420px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-right: 6px;
  background: var(--accent);
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

.nav-avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}

.nav-username {
  font-size: 13px;
  color: var(--fg);
}
</style>
