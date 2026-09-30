<template>
  <div class="plan-card" :class="{ 'is-confirmed': confirmed }">
    <div class="plan-card-header">
      <span class="plan-card-icon">🎯</span>
      <span class="plan-card-title">{{ confirmed ? '需求已确认' : '请确认这样理解对不对' }}</span>
      <span class="plan-card-badge" :class="complexityClass">{{ complexityLabel }}</span>
    </div>

    <div v-if="spec?.error" class="plan-error">
      {{ spec.error }}
    </div>

    <div class="plan-card-summary">
      <!-- 需求复述：用户第一眼应该看到"你理解对了没有" -->
      <div v-if="spec?.requirement_restated" class="plan-restated">
        {{ spec.requirement_restated }}
      </div>

      <!-- 功能清单 -->
      <div v-if="spec?.features?.length" class="plan-features">
        <div class="plan-label">会做这些事</div>
        <div class="plan-tags">
          <span v-for="(f, i) in spec.features" :key="i" class="plan-tag feature-tag">{{ f }}</span>
        </div>
      </div>

      <!-- 验收清单：用户签字的对象 = 系统判定通过与否的依据 -->
      <div v-if="acItems.length" class="plan-ac">
        <div class="plan-label">做完之后我会逐条这样检查</div>
        <ol class="ac-list">
          <li v-for="(ac, i) in acItems" :key="ac.id || i" class="ac-item" :class="acStateClass(ac)">
            <span class="ac-idx">{{ i + 1 }}</span>
            <div class="ac-body">
              <div class="ac-label-row">
                <span class="ac-label">{{ ac.label || ac.id }}</span>
                <span v-if="acState(ac) !== 'pending'" class="ac-state" :title="acStateTitle(ac)">
                  {{ acStateIcon(ac) }}
                </span>
              </div>
              <div v-if="ac.how_to_verify" class="ac-verify">{{ ac.how_to_verify }}</div>
            </div>
          </li>
        </ol>
      </div>

      <!-- 默认设置：让用户看到我们替他做了哪些决定 -->
      <div v-if="spec?.assumptions?.length" class="plan-assumptions">
        <div class="plan-label">你没提，我这样默认了</div>
        <ul class="assumption-list">
          <li v-for="(a, i) in spec.assumptions" :key="i">{{ a }}</li>
        </ul>
      </div>

      <!-- 工程细节：默认折叠。用户不必看懂这些，
           但技术型用户展开后仍有掌控感，也是出问题时唯一的排查入口。 -->
      <details v-if="hasTechDetails" class="tech-details">
        <summary class="tech-summary">技术细节（不影响你确认，可跳过）</summary>
        <div class="tech-body">
          <div v-if="techStackText" class="plan-tech">
            <div class="plan-label">技术栈</div>
            <span class="plan-tag tech-tag">{{ techStackText }}</span>
          </div>
          <div v-if="spec?.file_structure?.length" class="plan-files">
            <div class="plan-label">文件结构 ({{ spec.file_structure.length }} 个文件)</div>
            <div class="plan-file-list">
              <span v-for="(f, i) in spec.file_structure" :key="i" class="plan-file-item">{{ f }}</span>
            </div>
          </div>
        </div>
      </details>
    </div>

    <!-- 底部：待确认时放操作按钮，已确认时放确认页脚 -->
    <div class="plan-card-footer">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PlanSpec, AcceptanceCriterion } from '@/types/spec'

const props = withDefaults(
  defineProps<{
    /** 确认前来自 spec 事件，确认后来自 user 消息的 plan_confirmed */
    spec?: PlanSpec | null
    /** true = 已确认态（消息流里固定成一条）；false = 待确认态（可操作） */
    confirmed?: boolean
  }>(),
  { spec: null, confirmed: false }
)

// TL 的复杂度词表只有 simple|standard（见 tl_analysis.md），而徽章样式此前只认
// xs/s/m/l —— 两个集合没有交集，徽章一直没有底色，白字压在白底上等于看不见。
const COMPLEXITY_BANDS: Record<string, string> = {
  xs: 's', s: 's', simple: 's', low: 's',
  m: 'm', standard: 'm', medium: 'm', normal: 'm',
  l: 'l', xl: 'l', large: 'l', high: 'l', complex: 'l',
}

const COMPLEXITY_LABELS: Record<string, string> = {
  simple: '简单', standard: '标准', low: '简单', medium: '标准',
  high: '复杂', xs: '简单', s: '简单', m: '标准', l: '复杂', xl: '复杂', complex: '复杂',
}

const complexityClass = computed(() => {
  const raw = String(props.spec?.complexity ?? '').trim().toLowerCase()
  return `complexity-${COMPLEXITY_BANDS[raw] || 'm'}`
})

// 徽章原来直接显示英文原值（simple/standard），对正在确认需求的用户没有信息量
const complexityLabel = computed(() => {
  const raw = String(props.spec?.complexity ?? '').trim().toLowerCase()
  return COMPLEXITY_LABELS[raw] || raw.toUpperCase() || '标准'
})

const acItems = computed<AcceptanceCriterion[]>(
  () => (props.spec?.acceptance_criteria || []).filter(Boolean)
)

const techStackText = computed(() => {
  const ts = props.spec?.tech_stack
  if (!ts) return ''
  const parts: string[] = []
  if (ts.framework) parts.push(`框架: ${ts.framework}`)
  if (ts.css) parts.push(`CSS: ${ts.css}`)
  if (ts.storage) parts.push(`存储: ${ts.storage}`)
  return parts.join(' · ')
})

const hasTechDetails = computed(
  () => Boolean(techStackText.value || props.spec?.file_structure?.length)
)

// ---- AC 验收状态：让用户在同一张卡上看到"你确认过的这些条目，后来验得怎么样" ----
// 这不只是装饰：它把"签字"和"判定"这两件事在视觉上钉成同一条线。
type ACState = 'passed' | 'fail' | 'compromised' | 'unverified' | 'not_applicable' | 'pending'

function acState(ac: AcceptanceCriterion): ACState {
  if (ac.state) return ac.state as ACState
  if (ac.passed === true) return 'passed'
  if (ac.passed === false) return 'fail'
  return 'pending'
}

function acStateClass(ac: AcceptanceCriterion) {
  return `ac-${acState(ac)}`
}

function acStateIcon(ac: AcceptanceCriterion): string {
  switch (acState(ac)) {
    case 'passed': return '✅'
    case 'fail': return '❌'
    case 'compromised': return '⚠️'
    case 'unverified': return '❔'
    case 'not_applicable': return '➖'
    default: return ''
  }
}

function acStateTitle(ac: AcceptanceCriterion): string {
  switch (acState(ac)) {
    case 'passed': return '验收通过'
    case 'fail': return '未通过' + (ac.reason ? `：${ac.reason}` : '')
    case 'compromised': return '失败信号不可信：脚本未完整驱动页面，结论需人工核实'
    case 'unverified': return '未验证：断言前提不成立，既非通过也非失败'
    case 'not_applicable': return '不适用：该断言对当前实现未纳入验收'
    default: return ''
  }
}
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

.plan-error {
  font-size: 12px;
  line-height: 1.5;
  color: oklch(45% 0.15 25);
  background: color-mix(in srgb, oklch(45% 0.15 25) 10%, transparent);
  border: 1px solid color-mix(in srgb, oklch(45% 0.15 25) 30%, transparent);
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 12px;
}

.plan-card-summary {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 需求复述：整张卡里字号最大的一块，因为它回答的是"你理解对了没有" */
.plan-restated {
  font-size: 15px;
  font-weight: 600;
  color: var(--fg);
  line-height: 1.5;
  padding: 10px 12px;
  background: var(--bg);
  border-radius: 8px;
  border-left: 3px solid var(--accent);
}

.plan-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-bottom: 4px;
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
  background: color-mix(in srgb, oklch(50% 0.1 250) 12%, transparent);
  color: oklch(50% 0.1 250);
  border: 1px solid color-mix(in srgb, oklch(50% 0.1 250) 30%, transparent);
}

.tech-tag {
  background: color-mix(in srgb, oklch(55% 0.1 80) 12%, transparent);
  color: oklch(55% 0.1 80);
  border: 1px solid color-mix(in srgb, oklch(55% 0.1 80) 30%, transparent);
}

/* ---- 验收清单：让用户签字的对象和系统判定的对象在视觉上就是同一份 ---- */
.ac-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ac-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 8px;
  background: var(--bg);
  border-left: 3px solid transparent;
}

.ac-item.ac-passed { border-left-color: oklch(55% 0.1 155); }
.ac-item.ac-fail { border-left-color: oklch(55% 0.14 20); background: color-mix(in srgb, oklch(55% 0.14 20) 10%, transparent); }
.ac-item.ac-compromised { border-left-color: oklch(70% 0.14 70); background: color-mix(in srgb, oklch(70% 0.14 70) 12%, transparent); }
.ac-item.ac-unverified { border-left-color: oklch(60% 0.02 250); background: color-mix(in srgb, oklch(60% 0.02 250) 10%, transparent); }
.ac-item.ac-not_applicable { border-left-color: var(--border); opacity: 0.7; }

.ac-idx {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  margin-top: 1px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ac-body {
  flex: 1;
  min-width: 0;
}

.ac-label-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ac-label {
  font-size: 13px;
  color: var(--fg);
  line-height: 1.4;
  flex: 1;
  min-width: 0;
}

.ac-state {
  flex-shrink: 0;
  font-size: 12px;
  cursor: help;
}

.ac-verify {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.5;
  margin-top: 2px;
}

/* ---- 我替你定的默认设置 ---- */
.assumption-list {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--fg);
  line-height: 1.6;
}

/* ---- 工程细节折叠区 ---- */
.tech-details {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
}

.tech-summary {
  font-size: 12px;
  color: var(--muted);
  padding: 7px 10px;
  cursor: pointer;
  user-select: none;
  list-style: none;
}

.tech-summary::-webkit-details-marker {
  display: none;
}

.tech-summary::before {
  content: '▸ ';
}

.tech-details[open] .tech-summary::before {
  content: '▾ ';
}

.tech-body {
  padding: 0 10px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
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
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 1px 6px;
  border-radius: 4px;
}

.plan-card-footer {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
</style>
