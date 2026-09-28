<template>
  <div class="plan-card" :class="{ 'is-confirmed': confirmed }">
    <div class="plan-card-header">
      <span class="plan-card-icon">🎯</span>
      <span class="plan-card-title">{{ confirmed ? '开发计划已确认' : '开发计划确认' }}</span>
      <span class="plan-card-badge" :class="complexityClass">{{ spec?.complexity || 'S' }}</span>
    </div>

    <div class="plan-card-summary">
      <!-- 功能列表 -->
      <div v-if="spec?.features?.length" class="plan-features">
        <div class="plan-label">核心功能</div>
        <div class="plan-tags">
          <span v-for="(f, i) in spec.features" :key="i" class="plan-tag feature-tag">{{ f }}</span>
        </div>
      </div>

      <!-- 技术栈 -->
      <div v-if="techStackText" class="plan-tech">
        <div class="plan-label">技术栈</div>
        <span class="plan-tag tech-tag">{{ techStackText }}</span>
      </div>

      <!-- 文件结构 -->
      <div v-if="spec?.file_structure?.length" class="plan-files">
        <div class="plan-label">文件结构 ({{ spec.file_structure.length }} 个文件)</div>
        <div class="plan-file-list">
          <span v-for="(f, i) in spec.file_structure.slice(0, 6)" :key="i" class="plan-file-item">📄 {{ f }}</span>
          <span v-if="spec.file_structure.length > 6" class="plan-file-item more">
            …还有 {{ spec.file_structure.length - 6 }} 个文件
          </span>
        </div>
      </div>

      <!-- 数据模型 -->
      <div v-if="spec?.data_model" class="plan-data-model">
        <div class="plan-label">数据模型</div>
        <div class="plan-desc">{{ spec.data_model }}</div>
      </div>
    </div>

    <!-- 底部：待确认时放操作按钮，已确认时放确认页脚 -->
    <div class="plan-card-footer">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

/** 计划摘要卡的数据需求：确认前来自 spec 事件，确认后来自 user 消息的 plan_confirmed */
interface PlanCardSpec {
  features?: string[]
  tech_stack?: { framework?: string; css?: string; storage?: string }
  file_structure?: string[]
  data_model?: string
  complexity?: string
}

const props = withDefaults(
  defineProps<{
    spec?: PlanCardSpec | null
    /** true = 已确认态（消息流里固定成一条）；false = 待确认态（可操作） */
    confirmed?: boolean
  }>(),
  { spec: null, confirmed: false }
)

// TL 的复杂度词表只有 simple|standard（见 tl_analysis.md），而徽章样式此前只认
// xs/s/m/l —— 两个集合没有交集，徽章一直没有底色，白字压在白底上等于看不见。
// 这里统一归一到三档，未知取值退回「中」，保证任何取值下都可见。
const COMPLEXITY_BANDS: Record<string, string> = {
  xs: 's', s: 's', simple: 's', low: 's',
  m: 'm', standard: 'm', medium: 'm', normal: 'm',
  l: 'l', xl: 'l', large: 'l', high: 'l', complex: 'l',
}

const complexityClass = computed(() => {
  const raw = String(props.spec?.complexity ?? '').trim().toLowerCase()
  return `complexity-${COMPLEXITY_BANDS[raw] || 'm'}`
})

const techStackText = computed(() => {
  const ts = props.spec?.tech_stack
  if (!ts) return ''
  const parts: string[] = []
  if (ts.framework) parts.push(`框架: ${ts.framework}`)
  if (ts.css) parts.push(`CSS: ${ts.css}`)
  if (ts.storage) parts.push(`存储: ${ts.storage}`)
  return parts.join(' · ')
})
</script>

<style scoped>
/* 同一张卡片的两种状态：待确认（可操作，强调色边框）与已确认（固定成消息，成功色边框）。
   两种状态由同一个组件渲染，保证确认前后内容不缩水、样式不漂移。 */
.plan-card {
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: 12px;
  padding: 16px;
  margin: 0;
  align-self: stretch;
}

.plan-card.is-confirmed {
  border-color: oklch(55% 0.1 155);
  border-bottom-right-radius: 6px;
}

.plan-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.plan-card-icon {
  font-size: 18px;
}

.plan-card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
}

.plan-card-badge {
  margin-left: auto;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  color: #fff;
}

.complexity-s { background: oklch(55% 0.1 155); }
.complexity-m { background: oklch(65% 0.12 85); }
.complexity-l { background: oklch(50% 0.2 25); }

.plan-card-summary {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.plan-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-bottom: 2px;
}

.plan-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.plan-tag {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
  font-weight: 500;
}

.feature-tag {
  background: oklch(97% 0.01 250 / 0.5);
  color: oklch(50% 0.1 250);
  border: 1px solid oklch(85% 0.02 250);
}

.tech-tag {
  background: oklch(97% 0.01 80 / 0.5);
  color: oklch(55% 0.1 80);
  border: 1px solid oklch(85% 0.04 80);
}

.plan-file-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.plan-file-item {
  font-size: 11px;
  font-family: var(--font-mono, monospace);
  color: var(--muted);
  background: var(--bg);
  padding: 1px 6px;
  border-radius: 4px;
}

.plan-file-item.more {
  color: var(--accent);
  font-style: italic;
}

.plan-desc {
  font-size: 12px;
  color: var(--fg);
  line-height: 1.5;
  padding: 6px 10px;
  background: var(--bg);
  border-radius: 6px;
  white-space: pre-wrap;
}

.plan-card-footer {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
</style>
