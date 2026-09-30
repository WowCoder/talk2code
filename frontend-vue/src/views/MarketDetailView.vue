<template>
  <div class="detail-page">
    <AppNav />

    <main v-if="site" class="detail-main">
      <router-link class="back" to="/market">← 创意市集</router-link>

      <header class="head">
        <h1 class="title">{{ site.title }}</h1>
        <p class="author-row">
          <router-link class="author" :to="`/market/u/${site.author_id}`">
            {{ site.author || '匿名创作者' }}
          </router-link>
          <span class="dot">·</span>
          <span>{{ site.author_followers }} 粉丝</span>
          <button v-if="!isMine" class="follow" :class="{ on: site.following_author }"
                  @click="toggleFollow">
            {{ site.following_author ? '已关注' : '关注' }}
          </button>
        </p>
      </header>

      <!-- 大预览横幅：作品的第一印象，点击直接进站点 -->
      <a v-if="site.url" class="preview" :href="site.url" target="_blank" rel="noopener"
         :style="{ background: thumbColor }" :aria-label="`打开 ${site.title}`">
        <img v-if="site.thumb && !thumbFailed" class="preview-img" :src="site.thumb"
             :alt="site.title" @error="thumbFailed = true" />
        <span v-else class="preview-initial">{{ initial }}</span>
      </a>
      <div v-else class="preview no-link" :style="{ background: thumbColor }">
        <img v-if="site.thumb && !thumbFailed" class="preview-img" :src="site.thumb"
             :alt="site.title" @error="thumbFailed = true" />
        <span v-else class="preview-initial">{{ initial }}</span>
      </div>

      <div class="stats">
        <button class="like" :class="{ on: site.liked }" @click="toggleLike">
          <span class="like-icon" :class="{ on: site.liked }"></span>
          点赞 · {{ site.like_count }}
        </button>
        <a v-if="site.url" class="open" :href="site.url" target="_blank" rel="noopener">
          在新窗口打开 ↗
        </a>
        <HeatBadge :heat="site.heat" />
        <span class="stat">{{ site.comment_count }} 条留言</span>
      </div>

      <section class="comments">
        <h2 class="sec-title">留言</h2>

        <div v-if="authStore.isAuthenticated" class="composer">
          <textarea v-model="draft" class="input" rows="2" maxlength="200"
                    placeholder="说点什么（最多 200 字）"></textarea>
          <div class="composer-foot">
            <span class="counter">{{ draft.length }}/200</span>
            <button class="send" :disabled="sending || !draft.trim()" @click="send">
              {{ sending ? '发送中…' : '发送' }}
            </button>
          </div>
          <p v-if="err" class="err">{{ err }}</p>
        </div>
        <p v-else class="guest-tip">登录后就能留言和点赞。</p>

        <ul class="list">
          <li v-for="c in comments" :key="c.id" class="item">
            <div class="item-head">
              <span class="who">{{ c.author }}</span>
              <span class="when">{{ c.created_at?.slice(0, 10) }}</span>
              <button v-if="c.mine || isMine" class="del" @click="remove(c.id)">删除</button>
            </div>
            <p class="body">{{ c.body }}</p>
          </li>
        </ul>
        <p v-if="!comments.length" class="empty">还没有留言，来说第一句。</p>
      </section>
    </main>

    <p v-else-if="loadError" class="page-error">{{ loadError }}</p>
    <p v-else class="page-hint">加载中…</p>

    <div v-if="showNudge" class="nudge-anchor">
      <AuthNudge :title="nudgeTitle" @close="showNudge = false" @success="onAuthSuccess" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppNav from '@/components/layout/AppNav.vue'
import HeatBadge from '@/components/market/HeatBadge.vue'
import AuthNudge from '@/components/market/AuthNudge.vue'
import { useAuthStore } from '@/stores/auth'
import { useMarketGuest } from '@/composables/useMarketGuest'
import { useToast } from '@/composables/useToast'

interface SiteDetail {
  slug: string
  title: string
  author: string
  author_id: number
  author_followers: number
  following_author: boolean
  heat: number
  like_count: number
  comment_count: number
  listed_at: string | null
  url: string | null
  thumb: string | null
  liked: boolean
}
interface CommentItem {
  id: number
  author: string
  author_id: number
  body: string
  created_at: string | null
  mine: boolean
}
type Pending = 'like' | 'follow' | 'comment' | null

// 市集自带请求封装，不走 useApi：后者 401 会清登录态并跳 /login，
// 而匿名点赞/留言**预期**就是 401 —— 那是引导注册的时机，不是会话失效。
async function mfetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  const resp = await fetch(url, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...((options.headers as Record<string, string>) || {}) },
    ...options,
  })
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}))
    const err = new Error(e.error || e.message || `HTTP ${resp.status}`)
    ;(err as Error & { status?: number }).status = resp.status
    throw err
  }
  return resp.json() as Promise<T>
}

const route = useRoute()
const authStore = useAuthStore()
const { bumpGuestAction, clearGuestActions } = useMarketGuest()

const slug = String(route.params.slug || '')
const site = ref<SiteDetail | null>(null)
const comments = ref<CommentItem[]>([])
const draft = ref('')
const sending = ref(false)
const err = ref('')
const loadError = ref('')
const thumbFailed = ref(false)
const showNudge = ref(false)
const nudgeTitle = ref('登录就能点赞')
const pending = ref<Pending>(null)

// authStore 不暴露 userId（JWT 只靠 httpOnly cookie，前端不解析 token），
// 用用户名比对判断是否本人：用户名唯一，且这里只影响"删除"按钮的显示。
const isMine = computed(() => !!site.value && site.value.author === authStore.username)

const thumbColor = computed(() => {
  let h = 0
  for (const ch of slug) h = (h * 31 + ch.charCodeAt(0)) % 360
  const hue = 10 + (h % 50) // 暖色系窄区间，与设计稿一致
  return `linear-gradient(135deg, hsl(${hue} 72% 78%) 0%, hsl(${hue + 6} 58% 62%) 100%)`
})
const initial = computed(() => (site.value?.title || '?').trim().charAt(0).toUpperCase())

async function load() {
  try {
    site.value = await mfetch<SiteDetail>(`/api/market/sites/${slug}`)
    await loadComments()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载失败'
  }
}

async function loadComments() {
  const data = await mfetch<{ items: CommentItem[] }>(`/api/market/sites/${slug}/comments`)
  comments.value = data.items
}

function requireAuth(action: Pending, title: string): boolean {
  if (authStore.isDemo) {
    useToast().show('演示模式为只读，注册后即可参与互动', 'info')
    return false
  }
  if (authStore.isAuthenticated) return true
  if (bumpGuestAction(false)) {
    pending.value = action
    nudgeTitle.value = title
    showNudge.value = true
  }
  return false
}

async function toggleLike() {
  if (!site.value) return
  if (!requireAuth('like', '登录就能点赞')) return
  const was = site.value.liked
  site.value = { ...site.value, liked: !was, like_count: Math.max(0, site.value.like_count + (was ? -1 : 1)) }
  try {
    const r = await mfetch<{ like_count: number; liked: boolean }>(
      `/api/market/sites/${slug}/like`, { method: was ? 'DELETE' : 'POST' }
    )
    if (site.value) site.value = { ...site.value, liked: r.liked, like_count: r.like_count }
  } catch {
    await load()
  }
}

async function toggleFollow() {
  if (!site.value) return
  if (!requireAuth('follow', '登录就能关注')) return
  const was = site.value.following_author
  site.value = {
    ...site.value,
    following_author: !was,
    author_followers: Math.max(0, site.value.author_followers + (was ? -1 : 1)),
  }
  try {
    const r = await mfetch<{ following: boolean; followers: number }>(
      `/api/market/users/${site.value.author_id}/follow`, { method: was ? 'DELETE' : 'POST' }
    )
    if (site.value) site.value = { ...site.value, following_author: r.following, author_followers: r.followers }
  } catch {
    await load()
  }
}

async function send() {
  if (!requireAuth('comment', '登录就能留言')) return
  err.value = ''
  sending.value = true
  try {
    await mfetch(`/api/market/sites/${slug}/comments`, {
      method: 'POST',
      body: JSON.stringify({ body: draft.value }),
    })
    draft.value = ''
    await load()
    await loadComments()
  } catch (e) {
    err.value = e instanceof Error ? e.message : '发送失败'
  } finally {
    sending.value = false
  }
}

async function remove(id: number) {
  if (authStore.isDemo) {
    useToast().show('演示模式为只读，不可删除留言', 'info')
    return
  }
  try {
    await mfetch(`/api/market/sites/${slug}/comments/${id}`, { method: 'DELETE' })
    await load()
    await loadComments()
  } catch (e) {
    err.value = e instanceof Error ? e.message : '删除失败'
  }
}

async function onAuthSuccess() {
  const act = pending.value
  clearGuestActions()
  showNudge.value = false
  pending.value = null
  await load()
  await loadComments()
  // 注册后原地补上刚才那一下，不跳页、不丢上下文
  if (act === 'like' && site.value && !site.value.liked) await toggleLike()
  if (act === 'follow' && site.value && !site.value.following_author) await toggleFollow()
  if (act === 'comment' && draft.value.trim()) await send()
}

onMounted(load)
</script>

<style scoped>
.detail-page {
  min-height: 100vh;
  background: var(--bg);
  padding-bottom: 80px;
}

.back {
  display: inline-block;
  margin-bottom: 14px;
  font-size: 13px;
  color: var(--muted);
  text-decoration: none;
}

.back:hover { color: var(--accent); }

.detail-main {
  max-width: 760px;
  margin: 0 auto;
  padding: 24px;
}

.head {
  margin-bottom: 16px;
}

/* 大预览横幅：作品第一印象 */
.preview {
  display: flex;
  align-items: center;
  justify-content: center;
  aspect-ratio: 16 / 9;
  width: 100%;
  border-radius: 16px;
  overflow: hidden;
  text-decoration: none;
  border: 1px solid var(--border);
  margin-bottom: 14px;
}

.preview.no-link {
  pointer-events: none;
}

.preview-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.preview-initial {
  font-family: var(--font-display);
  font-size: 64px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.9);
}

.title {
  margin: 0 0 4px;
  font-size: 24px;
  font-weight: 700;
  color: var(--fg);
}

.author-row {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}

.author { color: var(--accent); text-decoration: none; }
.dot { opacity: .5; }

.follow {
  margin-left: 4px;
  padding: 2px 10px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
}

.follow.on {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}

.open {
  flex-shrink: 0;
  padding: 8px 14px;
  border-radius: 10px;
  background: var(--accent);
  color: var(--color-on-accent);
  font-size: 13px;
  text-decoration: none;
}

.stats {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 18px 0 24px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--border);
}

.like {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  cursor: pointer;
}

.like.on { color: var(--accent); border-color: var(--accent); }

.like-icon {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--border);
}

.like-icon.on { background: var(--accent); }

.stat { font-size: 13px; color: var(--muted); }

.sec-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--fg);
  margin: 0 0 12px;
}

.composer { margin-bottom: 18px; }

.input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--fg);
  font-size: 13px;
  font-family: inherit;
  resize: vertical;
}

.input:focus { outline: none; border-color: var(--accent); }

.composer-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}

.counter { font-size: 12px; color: var(--muted); }

.send {
  padding: 6px 16px;
  border: none;
  border-radius: 8px;
  background: var(--accent);
  color: var(--color-on-accent);
  font-size: 13px;
  cursor: pointer;
}

.send:disabled { opacity: .5; cursor: not-allowed; }

.guest-tip {
  font-size: 13px;
  color: var(--muted);
  margin: 0 0 16px;
}

.list { list-style: none; margin: 0; padding: 0; }

.item {
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}

.item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 4px;
}

.who { color: var(--fg); font-weight: 600; }

.del {
  margin-left: auto;
  border: none;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
}

.del:hover { color: var(--color-danger); }

.body {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--fg);
  white-space: pre-wrap;
  word-break: break-word;
}

.empty { font-size: 13px; color: var(--muted); }
.err { font-size: 12px; color: var(--color-danger); margin: 6px 0 0; }
.page-error { padding: 40px 24px; color: var(--color-danger); font-size: 13px; }
.page-hint { padding: 40px 24px; color: var(--muted); font-size: 13px; }

.nudge-anchor {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 60;
}
</style>
