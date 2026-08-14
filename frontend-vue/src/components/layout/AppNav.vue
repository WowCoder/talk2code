<template>
  <nav class="nav">
    <div class="nav-left">
      <template v-if="compact">
        <button class="nav-back" @click="$router.push('/history')">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M15 19l-7-7 7-7"/>
          </svg>
        </button>
        <span class="nav-title">{{ title }}</span>
      </template>
      <template v-else>
        <router-link to="/" class="nav-brand">
          <img src="@/assets/logo.png" alt="Talk2Code" class="nav-logo" /><span class="brand-text">Talk<span>2</span>Code</span>
        </router-link>
        <div class="nav-links">
          <router-link to="/history" class="nav-link" active-class="active">
            历史记录
          </router-link>
          <router-link to="/settings" class="nav-link" active-class="active">
            设置
          </router-link>
        </div>
      </template>
    </div>
    <div class="nav-right">
      <template v-if="compact && statusText">
        <span class="nav-status">
          <span v-if="isActive" class="status-dot"></span>
          {{ statusText }}
        </span>
      </template>
      <div class="nav-user" @click="toggleDropdown" ref="userRef">
        <span class="nav-avatar">{{ authStore.username[0]?.toUpperCase() }}</span>
        <span class="nav-username">{{ authStore.username }}</span>
        <svg class="nav-caret" :class="{ open: showDropdown }" viewBox="0 0 24 24" width="12" height="12"
             fill="none" stroke="currentColor" stroke-width="2.5"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M6 9l6 6 6-6"/>
        </svg>
      </div>
      <div v-if="showDropdown" class="nav-dropdown">
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
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useRouter } from 'vue-router'

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

const showDropdown = ref(false)
const userRef = ref<HTMLElement | null>(null)

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

onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))

async function handleLogout() {
  await authStore.logout()
  router.push('/login')
}
</script>

<style scoped>
.nav {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 0 24px;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
  backdrop-filter: blur(12px);
  background: oklch(99% 0.008 70 / 88%);
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
  border-radius: 6px;
}

.brand-text span {
  color: var(--accent);
}

.nav-links {
  display: flex;
  gap: 4px;
  margin-left: 16px;
}

.nav-link {
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--muted);
  transition: color 0.15s, background 0.15s;
}

.nav-link:hover {
  color: var(--fg);
  background: var(--bg);
}

.nav-link.active {
  color: var(--accent);
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
  background: var(--bg);
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
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}

.nav-user:hover {
  background: var(--bg);
}

.nav-caret {
  color: var(--muted);
  transition: transform 0.2s;
}

.nav-caret.open {
  transform: rotate(180deg);
}

.nav-dropdown {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 6px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 8px 24px oklch(0 0 0 / 10%);
  min-width: 150px;
  padding: 4px;
  z-index: 100;
  backdrop-filter: blur(12px);
}

.dropdown-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: none;
  border-radius: 7px;
  font-size: 13px;
  font-family: var(--font-body);
  color: var(--fg);
  cursor: pointer;
  transition: background 0.15s;
}

.dropdown-item:hover {
  background: var(--bg);
}

.logout-item:hover {
  color: oklch(55% 0.15 20);
  background: oklch(55% 0.15 20 / 8%);
}

.nav-status {
  font-size: 12px;
  color: var(--muted);
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

.nav-avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
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
