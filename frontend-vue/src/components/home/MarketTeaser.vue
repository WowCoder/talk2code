<template>
  <!-- 市集为空时不渲染整块（含标题）：一个指向空列表的入口只会让人点进去失望。 -->
  <section v-if="items.length" class="teaser">
    <div class="teaser-inner">
      <header class="teaser-head">
        <div>
          <span class="teaser-eyebrow">CREATIVE MARKET</span>
          <h2 class="teaser-title">看看别人做成了什么</h2>
        </div>
        <router-link class="teaser-more" to="/market">逛全部市集 →</router-link>
      </header>
      <div class="teaser-list">
        <router-link v-for="s in items" :key="s.slug" class="teaser-card" :to="`/market/${s.slug}`">
          <span class="teaser-thumb" :style="{ background: colorOf(s.slug) }">
            <img v-if="s.thumb && !failed.has(s.slug)" class="teaser-img" :src="s.thumb"
                 :alt="s.title" loading="lazy" @error="failed.add(s.slug)" />
          </span>
          <span class="teaser-name">{{ s.title }}</span>
          <span class="teaser-meta">@{{ s.author || '匿名' }} · 热度 {{ s.like_count }}</span>
        </router-link>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useApi } from '@/composables/useApi'

interface TeaserSite {
  slug: string
  title: string
  author: string
  like_count: number
  thumb: string | null
}

const { api } = useApi()
const items = ref<TeaserSite[]>([])
// 缩略图加载失败集合：整卡回落到色块，不做重试（截图本来就是装饰）
const failed = reactive(new Set<string>())

// 封面底色用暖色系的柔和渐变（与设计稿一致），不再是随机高饱和色块
function colorOf(slug: string) {
  let h = 0
  for (const ch of slug) h = (h * 31 + ch.charCodeAt(0)) % 360
  const hue = 10 + (h % 50) // 陶土红 ~ 玫瑰色的窄区间
  return `linear-gradient(135deg, hsl(${hue} 72% 78%) 0%, hsl(${hue + 6} 58% 62%) 100%)`
}

onMounted(async () => {
  try {
    // 只取最热的几个：这里是给输入框一点"能做出什么"的实感，不是第二个市集页面。
    const data = await api<{ items: TeaserSite[] }>(
      '/api/market/sites?sort=hot&page_size=4'
    )
    items.value = data.items ?? []
  } catch {
    // 引流块拿不到就不显示 —— 它不参与主流程，静默失败优于报错打扰
    items.value = []
  }
})
</script>

<style scoped>
.teaser {
  margin-top: 56px;
  padding: 24px 0 64px;
  background: var(--bg);
  border-top: 1px solid var(--border);
}

.teaser-inner {
  max-width: 1160px;
  margin: 0 auto;
  padding: 0 32px;
}

.teaser-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 20px;
}

.teaser-eyebrow {
  display: block;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.18em;
  color: var(--faint);
  margin-bottom: 4px;
}

.teaser-title {
  margin: 0;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  color: var(--fg);
}

.teaser-more {
  font-size: 13px;
  font-weight: 500;
  color: var(--accent-strong);
  text-decoration: none;
}

.teaser-more:hover { text-decoration: underline; }

.teaser-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 20px;
}

.teaser-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 12px 14px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  text-decoration: none;
  transition: border-color 0.16s ease, transform 0.16s ease, box-shadow 0.16s ease;
}

.teaser-card:hover {
  border-color: rgba(207, 106, 95, 0.45);
  transform: translateY(-2px);
  box-shadow: 0 10px 24px rgba(34, 23, 19, 0.08);
}

.teaser-thumb {
  display: block;
  height: 150px;
  border-radius: 9px;
  overflow: hidden;
  /* 白底作品不加边框会和「图没加载出来」难以区分 */
  border-bottom: 1px solid var(--border);
}

.teaser-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.teaser-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.teaser-meta {
  font-size: 12px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
