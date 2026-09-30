<template>
  <div class="market-page">
    <!-- AppNav 自带游客态：只留市集可用、其余 tab 置灰、右上角给登录入口。
         市集页不再各写一份 guest nav —— 两处渲染同一条导航栏，改一处漏一处
         必然出现「游客看到的导航和登录后长得不一样」。 -->
    <AppNav />

    <main class="market-main">
      <!-- Hero：一句副标题说明「这里的东西怎么来的」，比空喊 slogan 有说服力 -->
      <header class="market-hero">
        <span class="hero-eyebrow">CREATIVE MARKET</span>
        <h1 class="market-title">大家做出来的应用</h1>
        <p class="hero-sub">每一件都来自一次真实的需求 · 作者说公开才算数</p>
      </header>

      <div class="cats">
        <button class="cat" :class="{ on: !category }" @click="category = ''">
          全部<template v-if="total"> {{ total }}</template>
        </button>
        <button v-for="c in categories" :key="c" class="cat" :class="{ on: category === c }"
                @click="category = c">{{ catLabel(c) }}</button>
        <span class="sort-group">
          <button class="cat" :class="{ on: sort === 'hot' && !week }" @click="setSort('hot')">热度 ↓</button>
          <button class="cat" :class="{ on: sort === 'new' && !week }" @click="setSort('new')">最新</button>
          <button class="cat" :class="{ on: week }" @click="toggleWeek">本周</button>
        </span>
      </div>

      <p v-if="loadError" class="market-error">{{ loadError }}</p>

      <div v-if="loading" class="market-grid">
        <div v-for="i in 6" :key="i" class="skel-card"></div>
      </div>

      <div v-else-if="items.length" class="market-grid">
        <SiteCard v-for="s in items" :key="s.slug" :site="s" @like="onLike" />
      </div>

      <div v-else class="market-empty">
        <p class="empty-title">市集还没有作品</p>
        <p class="empty-sub">发布你的需求后，在发布面板勾选「同步到创意市集」就能出现在这里。</p>
      </div>

      <div v-if="pendingSlug" class="nudge-anchor">
        <AuthNudge title="登录就能点赞" @close="closeNudge" @success="onAuthSuccess" />
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import AppNav from '@/components/layout/AppNav.vue'
import SiteCard, { type MarketSite } from '@/components/market/SiteCard.vue'
import AuthNudge from '@/components/market/AuthNudge.vue'
import { useAuthStore } from '@/stores/auth'
import { useMarketGuest } from '@/composables/useMarketGuest'
import { useToast } from '@/composables/useToast'

// 市集自带请求封装，不走 useApi：useApi 在 401 时会清空登录态并跳 /login，
// 而市集的匿名点赞**预期**就是 401 —— 那是引导注册的时机，不是会话失效。
async function mfetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  const resp = await fetch(url, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...((options.headers as Record<string, string>) || {}) },
    ...options,
  })
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}))
    const e = new Error(err.error || err.message || `HTTP ${resp.status}`)
    ;(e as Error & { status?: number }).status = resp.status
    throw e
  }
  return resp.json() as Promise<T>
}

const authStore = useAuthStore()
const { bumpGuestAction, clearGuestActions } = useMarketGuest()

// 分类中文名：枚举值由后端下发（categories 字段），这里只做展示映射。
const CAT_LABELS: Record<string, string> = {
  game: '游戏', tool: '工具', admin: '后台', landing: '展示', other: '其它',
}
function catLabel(c: string) {
  return CAT_LABELS[c] || c
}

const categories = ref<string[]>([])
const category = ref('')
const week = ref(false)

function setSort(s: 'hot' | 'new') {
  sort.value = s
  week.value = false
}

function toggleWeek() {
  week.value = !week.value
  if (week.value) sort.value = 'hot'
}
const items = ref<MarketSite[]>([])
const total = ref(0)
const sort = ref<'hot' | 'new'>('hot')
const loading = ref(true)
const loadError = ref('')
const pendingSlug = ref('')

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const params = new URLSearchParams({ sort: sort.value })
    if (week.value) params.set('window', 'week')
    if (category.value) params.set('category', category.value)
    const data = await mfetch<{ items: MarketSite[]; total: number; categories?: string[] }>(
      `/api/market/sites?${params.toString()}`
    )
    items.value = data.items
    total.value = data.total
    if (data.categories?.length) categories.value = data.categories
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function onLike(site: MarketSite) {
  if (authStore.isDemo) {
    useToast().show('演示模式为只读，注册后即可点赞', 'info')
    return
  }
  if (!authStore.isAuthenticated) {
    if (bumpGuestAction(false)) pendingSlug.value = site.slug
    return
  }
  const idx = items.value.findIndex((x) => x.slug === site.slug)
  const wasLiked = site.liked
  // 乐观更新：失败再回滚
  if (idx >= 0) {
    items.value[idx] = {
      ...site,
      liked: !wasLiked,
      like_count: Math.max(0, site.like_count + (wasLiked ? -1 : 1)),
    }
  }
  try {
    const res = await mfetch<{ like_count: number; liked: boolean }>(
      `/api/market/sites/${site.slug}/like`,
      { method: wasLiked ? 'DELETE' : 'POST' }
    )
    if (idx >= 0) {
      items.value[idx] = { ...items.value[idx], liked: res.liked, like_count: res.like_count }
    }
  } catch (e) {
    if (idx >= 0) items.value[idx] = { ...site }
  }
}

function closeNudge() {
  pendingSlug.value = ''
}

async function onAuthSuccess() {
  const slug = pendingSlug.value
  clearGuestActions()
  pendingSlug.value = ''
  await load()
  // 注册后原地补上刚才那一下点赞，不跳页、不丢上下文
  if (slug) {
    const target = items.value.find((x) => x.slug === slug)
    if (target && !target.liked) await onLike(target)
  }
}

onMounted(load)
watch([sort, week, category], load)
</script>

<style scoped>
.market-page {
  min-height: 100vh;
  background: var(--bg);
}

.market-main {
  max-width: 1080px;
  margin: 0 auto;
  padding: 28px 24px 80px;
}

/* ===== Hero ===== */
.market-hero {
  text-align: center;
  padding: 36px 0 26px;
}

.hero-eyebrow {
  display: block;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.2em;
  color: var(--faint);
  margin-bottom: 8px;
}

.market-title {
  font-family: var(--font-display);
  font-size: 30px;
  font-weight: 700;
  color: var(--fg);
  margin: 0 0 10px;
  letter-spacing: -0.01em;
}

.hero-sub {
  font-size: 13.5px;
  color: var(--muted);
  margin: 0;
}

/* ===== 分类 chips（active 红底白字）===== */
.cats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 20px;
  align-items: center;
}

.sort-group {
  display: inline-flex;
  gap: 8px;
  margin-left: auto;
}

.cat {
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  color: var(--muted);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}

.cat:hover {
  color: var(--fg);
  border-color: var(--faint);
}

.cat.on {
  border-color: var(--accent);
  color: #fff;
  background: var(--accent);
  font-weight: 600;
}

.market-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(232px, 1fr));
  gap: 16px;
}

.skel-card {
  height: 196px;
  border-radius: 12px;
  background: var(--surface);
  border: 1px solid var(--border);
}

.market-empty {
  padding: 64px 0;
  text-align: center;
}

.empty-title {
  font-size: 15px;
  color: var(--fg);
  margin: 0 0 6px;
}

.empty-sub {
  font-size: 13px;
  color: var(--muted);
  margin: 0;
}

.market-error {
  color: var(--color-danger);
  font-size: 13px;
}

.nudge-anchor {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 60;
}
</style>
