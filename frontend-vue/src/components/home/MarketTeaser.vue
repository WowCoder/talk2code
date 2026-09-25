<template>
  <!-- 市集为空时不渲染整块（含标题）：一个指向空列表的入口只会让人点进去失望。 -->
  <section v-if="items.length" class="teaser">
    <header class="teaser-head">
      <h2 class="teaser-title">大家在做些什么</h2>
      <router-link class="teaser-more" to="/market">去创意市集 →</router-link>
    </header>
    <div class="teaser-list">
      <router-link v-for="s in items" :key="s.slug" class="teaser-card" :to="`/market/${s.slug}`">
        <span class="teaser-thumb" :style="{ background: colorOf(s.slug) }">
          <img v-if="s.thumb && !failed.has(s.slug)" class="teaser-img" :src="s.thumb"
               :alt="s.title" loading="lazy" @error="failed.add(s.slug)" />
        </span>
        <span class="teaser-name">{{ s.title }}</span>
        <span class="teaser-meta">{{ s.like_count }} 赞 · {{ s.author || '匿名' }}</span>
      </router-link>
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

function colorOf(slug: string) {
  let h = 0
  for (const ch of slug) h = (h * 31 + ch.charCodeAt(0)) % 360
  return `hsl(${h} 58% 58%)`
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
  margin-top: 28px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
}

.teaser-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 12px;
}

.teaser-title {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--fg);
}

.teaser-more {
  font-size: 12px;
  color: var(--accent);
  text-decoration: none;
}

.teaser-more:hover { text-decoration: underline; }

.teaser-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}

.teaser-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  text-decoration: none;
  transition: border-color 0.16s ease, transform 0.16s ease;
}

.teaser-card:hover {
  border-color: var(--accent);
  transform: translateY(-2px);
}

.teaser-thumb {
  display: block;
  height: 68px;
  border-radius: 7px;
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
  font-size: 12px;
  font-weight: 600;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.teaser-meta {
  font-size: 11px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
