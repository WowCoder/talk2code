<template>
  <article class="card">
    <!-- 整卡进详情页：先让人看作品信息、留言与作者，再由详情页「打开站点」跳转。
         直接跳外链会把留言/关注这两个入口绕过去。 -->
    <!-- 缩略图：优先用真实截图，加载失败（未生成/截不到）回退到色块。
         用 @error 而不是预先判断 —— 服务端是否已生成只有请求了才知道。 -->
    <router-link class="thumb" :to="`/market/${site.slug}`"
                 :style="{ background: thumbColor }" :aria-label="site.title">
      <img v-if="site.thumb && !thumbFailed" class="thumb-img" :src="site.thumb"
           :alt="site.title" loading="lazy" @error="thumbFailed = true" />
      <span v-else class="thumb-initial">{{ initial }}</span>
    </router-link>

    <div class="card-body">
      <router-link class="title" :to="`/market/${site.slug}`" :title="site.title">
        {{ site.title }}
      </router-link>
      <router-link class="author" :to="`/market/u/${site.author_id ?? ''}`">
        @{{ site.author || '匿名创作者' }}
      </router-link>

      <div class="card-foot">
        <HeatBadge :heat="site.heat" />
        <div class="counts">
          <button class="like" :class="{ on: liked }" :disabled="busy" @click="onLike">
            <span class="like-icon" :class="{ on: liked }"></span>
            <span>{{ site.like_count }}</span>
          </button>
          <span class="cmt">{{ site.comment_count }}</span>
        </div>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import HeatBadge from './HeatBadge.vue'

export interface MarketSite {
  slug: string
  title: string
  author: string
  author_id?: number
  heat: number
  like_count: number
  comment_count: number
  listed_at: string | null
  url: string | null
  thumb: string | null
  category?: string
  liked: boolean
}

const props = defineProps<{ site: MarketSite }>()
const emit = defineEmits<{ like: [site: MarketSite] }>()

const busy = ref(false)
const liked = computed(() => props.site.liked)
const thumbFailed = ref(false)

// 缩略图占位：按 slug 派生稳定色。暖色系柔和渐变（与设计稿一致），
// 同一作品每次进来颜色一致，不会看起来像随机闪烁。
const thumbColor = computed(() => {
  let h = 0
  for (const ch of props.site.slug) h = (h * 31 + ch.charCodeAt(0)) % 360
  const hue = 10 + (h % 50) // 陶土红 ~ 玫瑰色的窄区间
  return `linear-gradient(135deg, hsl(${hue} 72% 78%) 0%, hsl(${hue + 6} 58% 62%) 100%)`
})

const initial = computed(() => (props.site.title || '?').trim().charAt(0).toUpperCase())

async function onLike() {
  if (busy.value) return
  busy.value = true
  try {
    emit('like', props.site)
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.card {
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  transition: border-color 0.16s ease, transform 0.16s ease;
}

.card:hover {
  border-color: var(--accent);
  transform: translateY(-2px);
}

.thumb {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 108px;
  text-decoration: none;
  /* 大量作品是白底页面，截图区域不加边框会和「图片没加载出来」难以区分 */
  border-bottom: 1px solid var(--border);
  overflow: hidden;
}

.thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.thumb-initial {
  font-family: var(--font-display);
  font-size: 34px;
  font-weight: 700;
  color: #fff;
  opacity: 0.92;
}

.card-body {
  padding: 12px 14px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
}

.title {
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
  margin: 0;
  line-height: 1.5;
  text-decoration: none;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.title:hover { color: var(--accent); }

.author {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
  text-decoration: none;
}

.author:hover { color: var(--accent); }

.card-foot {
  margin-top: auto;
  padding-top: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.like {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
  transition: color 0.16s ease, border-color 0.16s ease;
}

.like:hover:not(:disabled) {
  color: var(--accent);
  border-color: var(--accent);
}

.like.on {
  color: var(--accent);
  border-color: var(--accent);
}

.like-icon {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--border);
}

.like-icon.on {
  background: var(--accent);
}

.counts {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.cmt {
  font-size: 12px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.cmt::before {
  content: '';
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 4px;
  border-radius: 2px;
  background: var(--border);
}
</style>
