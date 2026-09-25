<template>
  <div class="author-page">
    <AppNav />

    <main v-if="author" class="author-main">
      <router-link class="back" to="/market">← 创意市集</router-link>

      <header class="head">
        <span class="avatar">{{ initial }}</span>
        <div class="meta">
          <h1 class="name">{{ author.username }}</h1>
          <p class="counts">
            <span>{{ author.site_count }} 个作品</span>
            <span class="dot">·</span>
            <span>{{ author.followers }} 粉丝</span>
            <span class="dot">·</span>
            <span>关注 {{ author.following }}</span>
          </p>
        </div>
        <button v-if="authStore.isAuthenticated && !isSelf" class="follow"
                :class="{ on: author.followed_by_me }" @click="toggleFollow">
          {{ author.followed_by_me ? '已关注' : '关注' }}
        </button>
      </header>

      <h2 class="sec-title">已上架作品</h2>
      <div v-if="items.length" class="grid">
        <SiteCard v-for="s in items" :key="s.slug" :site="s" @like="onLike" />
      </div>
      <p v-else class="empty">这位创作者还没有上架作品。</p>
    </main>

    <p v-else-if="loadError" class="page-error">{{ loadError }}</p>
    <p v-else class="page-hint">加载中…</p>

    <div v-if="showNudge" class="nudge-anchor">
      <AuthNudge title="登录就能关注" @close="showNudge = false" @success="onAuthSuccess" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppNav from '@/components/layout/AppNav.vue'
import SiteCard, { type MarketSite } from '@/components/market/SiteCard.vue'
import AuthNudge from '@/components/market/AuthNudge.vue'
import { useAuthStore } from '@/stores/auth'
import { useMarketGuest } from '@/composables/useMarketGuest'

interface AuthorInfo {
  id: number
  username: string
  site_count: number
  followers: number
  following: number
  followed_by_me: boolean
}

async function mfetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  const resp = await fetch(url, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...((options.headers as Record<string, string>) || {}) },
    ...options,
  })
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}))
    throw new Error(e.error || e.message || `HTTP ${resp.status}`)
  }
  return resp.json() as Promise<T>
}

const route = useRoute()
const authStore = useAuthStore()
const { bumpGuestAction, clearGuestActions } = useMarketGuest()

const uid = String(route.params.id || '')
const author = ref<AuthorInfo | null>(null)
const items = ref<MarketSite[]>([])
const loadError = ref('')
const showNudge = ref(false)

const initial = computed(() => (author.value?.username || '?').charAt(0).toUpperCase())
const isSelf = computed(() => author.value?.username === authStore.username)

async function load() {
  loadError.value = ''
  try {
    author.value = await mfetch<AuthorInfo>(`/api/market/users/${uid}`)
    const data = await mfetch<{ items: MarketSite[] }>('/api/market/sites?sort=new&page_size=50')
    // 后端没有按作者过滤的列表端点（首版不需要），在这里筛 —— 作者作品量级很小。
    items.value = data.items.filter((i) => i.author === author.value?.username)
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载失败'
  }
}

async function toggleFollow() {
  if (!author.value) return
  if (!authStore.isAuthenticated) {
    if (bumpGuestAction(false)) showNudge.value = true
    return
  }
  const was = author.value.followed_by_me
  author.value = {
    ...author.value,
    followed_by_me: !was,
    followers: Math.max(0, author.value.followers + (was ? -1 : 1)),
  }
  try {
    const r = await mfetch<{ following: boolean; followers: number }>(
      `/api/market/users/${uid}/follow`, { method: was ? 'DELETE' : 'POST' }
    )
    if (author.value) author.value = { ...author.value, followed_by_me: r.following, followers: r.followers }
  } catch {
    await load()
  }
}

async function onLike(site: MarketSite) {
  if (!authStore.isAuthenticated) return
  const idx = items.value.findIndex((x) => x.slug === site.slug)
  const was = site.liked
  if (idx >= 0) {
    items.value[idx] = { ...site, liked: !was, like_count: Math.max(0, site.like_count + (was ? -1 : 1)) }
  }
  try {
    const r = await mfetch<{ like_count: number; liked: boolean }>(
      `/api/market/sites/${site.slug}/like`, { method: was ? 'DELETE' : 'POST' }
    )
    if (idx >= 0) items.value[idx] = { ...items.value[idx], liked: r.liked, like_count: r.like_count }
  } catch {
    await load()
  }
}

async function onAuthSuccess() {
  const wantFollow = !author.value?.followed_by_me
  clearGuestActions()
  showNudge.value = false
  await load()
  if (wantFollow) await toggleFollow()
}

onMounted(load)
</script>

<style scoped>
.author-page { min-height: 100vh; background: var(--bg); padding-bottom: 80px; }
.back { display: inline-block; margin-bottom: 14px; font-size: 13px; color: var(--muted); text-decoration: none; }
.back:hover { color: var(--accent); }

.author-main { max-width: 1080px; margin: 0 auto; padding: 24px; }

.head { display: flex; align-items: center; gap: 14px; margin-bottom: 28px; }

.avatar {
  width: 52px; height: 52px; border-radius: 50%;
  background: var(--accent-soft); color: var(--accent);
  display: flex; align-items: center; justify-content: center;
  font-family: var(--font-display); font-size: 22px; font-weight: 700;
}

.meta { flex: 1; }
.name { margin: 0 0 2px; font-size: 19px; font-weight: 700; color: var(--fg); }
.counts { margin: 0; display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--muted); }
.dot { opacity: .5; }

.follow {
  padding: 6px 16px; border: 1px solid var(--border); border-radius: 999px;
  background: transparent; color: var(--muted); font-size: 13px; cursor: pointer;
}
.follow.on { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }

.sec-title { font-size: 15px; font-weight: 600; color: var(--fg); margin: 0 0 14px; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(232px, 1fr)); gap: 16px; }

.empty { font-size: 13px; color: var(--muted); }
.page-error { padding: 40px 24px; color: var(--color-danger); font-size: 13px; }
.page-hint { padding: 40px 24px; color: var(--muted); font-size: 13px; }
.nudge-anchor { position: fixed; right: 24px; bottom: 24px; z-index: 60; }
</style>
