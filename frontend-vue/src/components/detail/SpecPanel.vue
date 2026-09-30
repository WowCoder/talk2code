<template>
  <div class="spec-panel">
    <div class="spec-header">
      SPEC
      <span v-if="specData?.complexity" :class="['complexity-badge', specData.complexity.toLowerCase()]">
        {{ specData.complexity }}
      </span>
    </div>
    <div v-if="specData" class="spec-body">
      <!-- 需求复述 -->
      <div v-if="specData.requirement_restated" class="spec-section">
        <div class="section-title">需求重述</div>
        <div class="spec-restated">{{ specData.requirement_restated }}</div>
      </div>

      <!-- 核心功能 -->
      <div v-if="specData.features?.length" class="spec-section">
        <div class="section-title">核心功能</div>
        <div class="feature-list">
          <span v-for="(f, i) in specData.features" :key="i" class="feature-tag">{{ f }}</span>
        </div>
      </div>

      <!-- 默认设置 -->
      <div v-if="specData.assumptions?.length" class="spec-section">
        <div class="section-title">默认设置</div>
        <ul class="assumption-list">
          <li v-for="(a, i) in specData.assumptions" :key="i">{{ a }}</li>
        </ul>
      </div>

      <!-- 验收条件 -->
      <div v-if="specData.acceptance_criteria?.length" class="spec-section">
        <div class="section-title">验收条件 · {{ specData.acceptance_criteria.length }} 条</div>
        <div class="ac-section">
          <div
            v-for="(item, i) in specData.acceptance_criteria"
            :key="i"
            :class="['ac-item', acState(item)]"
          >
            <span :class="['ac-status', acState(item)]">
              {{ acIcon(item) }}
            </span>
            <span class="ac-label">{{ item.id }}: {{ item.label }}</span>
            <div v-if="item.how_to_verify" class="ac-verify">验证: {{ item.how_to_verify }}</div>
            <!-- 四态信号各自的判定说明（P4 可视化） -->
            <div v-if="acState(item) === 'fail' && item.reason" class="ac-reason">
              {{ item.reason }}
            </div>
            <div v-else-if="acState(item) === 'compromised'" class="ac-reason ac-warn">
              失败信号不可信：脚本未完整驱动页面（如点击步骤超时），后续断言可能是在未操作状态下得出的。请先核实该缺陷是否真实存在，勿直接照此修改。
            </div>
            <div v-else-if="acState(item) === 'unverified'" class="ac-reason ac-muted">
              断言前提不成立（未触发验证），既非通过也非失败，需结合代码与截图自行判断。
            </div>
            <div v-else-if="acState(item) === 'not_applicable'" class="ac-reason ac-muted">
              该断言对当前实现不适用，未纳入验收。
            </div>
          </div>
        </div>
      </div>

      <!-- 文件结构 -->
      <div v-if="specData.file_structure?.length" class="spec-section">
        <div class="section-title">文件结构 · {{ specData.file_structure.length }} 个文件</div>
        <div class="file-tree">
          <div
            v-for="(file, i) in specData.file_structure"
            :key="i"
            class="file-tree-file"
          >
            {{ file }}
          </div>
        </div>
      </div>

      <!-- 技术栈：工程细节排在最后 -->
      <div v-if="techStackItems.length" class="spec-section">
        <div class="section-title">技术栈</div>
        <div class="tech-stack">
          <span v-for="(item, i) in techStackItems" :key="i" class="tech-badge">{{ item }}</span>
        </div>
      </div>
    </div>
    <div v-else class="spec-empty">
      等待 SPEC...
    </div>

    <!-- Evaluator 评估结果 -->
    <div v-if="evaluatorResult" class="evaluator-section">
      <div class="evaluator-header">
        {{ evaluatorResult.verdict === 'PASS' ? '✅' : '❌' }} 代码评估: {{ evaluatorResult.verdict }}
      </div>
      <div class="evaluator-summary">{{ evaluatorResult.summary }}</div>
      <div class="evaluator-scores">
        <div
          v-for="(val, key) in evaluatorResult.score"
          :key="key"
          class="score-item"
        >
          <span class="score-dim">{{ key }}</span>
          <span class="score-bar-bg">
            <span
              class="score-bar-fill"
              :style="{ width: (val || 0) * 10 + '%' }"
              :class="scoreClass(val || 0)"
            ></span>
          </span>
          <span class="score-val">{{ val }}/10</span>
        </div>
      </div>
      <div v-if="evaluatorResult.findings?.length" class="evaluator-findings">
        <div class="findings-title">发现的问题 ({{ evaluatorResult.findings.length }})</div>
        <div
          v-for="(f, i) in evaluatorResult.findings"
          :key="i"
          :class="['finding-item', f.severity]"
        >
          <span class="finding-severity">{{ severityLabel(f.severity) }}</span>
          <span class="finding-desc">{{ f.description }}</span>
          <div v-if="f.suggestion" class="finding-suggestion">💡 {{ f.suggestion }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { SSEEvaluatorResultData } from '@/types/sse'
import type { PlanSpec, AcceptanceCriterion } from '@/types/spec'

export type SpecData = PlanSpec

const props = defineProps<{
  specData?: SpecData | null
  evaluatorResult?: SSEEvaluatorResultData | null
}>()

const techStackItems = computed(() => {
  const ts = props.specData?.tech_stack
  if (!ts) return []
  const items: string[] = []
  if (ts.framework) items.push(`框架: ${ts.framework}`)
  if (ts.css) items.push(`CSS: ${ts.css}`)
  if (ts.storage) items.push(`存储: ${ts.storage}`)
  return items
})

// 四态信号（P4）：后端直接给 state 时优先采用；旧后端只给 passed 布尔时回退推导。
type ACState = 'passed' | 'fail' | 'compromised' | 'unverified' | 'not_applicable' | 'pending'

function acState(item: AcceptanceCriterion): ACState {
  if (item.state) return item.state as ACState
  if (item.passed === true) return 'passed'
  if (item.passed === false) return 'fail'
  return 'pending'
}

function acIcon(item: AcceptanceCriterion): string {
  switch (acState(item)) {
    case 'passed': return '✅'
    case 'fail': return '❌'
    case 'compromised': return '⚠️'
    case 'unverified': return '❔'
    case 'not_applicable': return '➖'
    default: return '⏳'
  }
}

function scoreClass(val: number): string {
  if (val >= 7) return 'good'
  if (val >= 4) return 'medium'
  return 'bad'
}

function severityLabel(severity: string): string {
  const map: Record<string, string> = {
    critical: '🔴',
    major: '🟠',
    minor: '🟡',
  }
  return map[severity] || '⚪'
}
</script>

<style scoped>
.spec-panel {
  background: var(--wb-surface);
  border: 1px solid var(--wb-border);
  border-radius: 10px;
  overflow-y: auto;
}

.spec-header {
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--wb-fg);
  border-bottom: 1px solid var(--wb-border);
  display: flex;
  align-items: center;
  gap: 8px;
}

.complexity-badge {
  margin-left: auto;
  font-size: 10px;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 999px;
  color: #fff;
}

.complexity-badge.simple {
  background: oklch(55% 0.1 155);
}

.complexity-badge.standard {
  background: oklch(50% 0.2 25);
}

.spec-body {
  padding: 0;
}

.spec-section {
  padding: 8px 0;
  border-bottom: 1px solid var(--wb-border);
}

.spec-section:last-child {
  border-bottom: none;
}

.section-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--wb-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0 14px 4px;
}

/* 核心功能 */
.feature-list {
  padding: 0 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.feature-tag {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
  /* 以文字色为基色做低透明度衬底：浅/深主题下都保持可读 */
  background: color-mix(in srgb, oklch(50% 0.1 250) 12%, transparent);
  color: oklch(50% 0.1 250);
  border: 1px solid color-mix(in srgb, oklch(50% 0.1 250) 30%, transparent);
}

/* 技术栈 */
.tech-stack {
  padding: 0 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.tech-badge {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, oklch(55% 0.1 80) 12%, transparent);
  color: oklch(55% 0.1 80);
  border: 1px solid color-mix(in srgb, oklch(55% 0.1 80) 30%, transparent);
}

/* 需求复述 / 数据模型 / 实现注意事项 */
.spec-text {
  padding: 0 14px;
  font-size: 12px;
  color: var(--wb-fg);
  line-height: 1.5;
}

.spec-restated {
  padding: 0 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--wb-fg);
  line-height: 1.5;
}

.assumption-list {
  margin: 0;
  padding: 0 14px 0 30px;
  font-size: 12px;
  color: var(--wb-fg);
  line-height: 1.6;
}

/* 验收条件 */
.ac-section {
  padding: 0;
}

.ac-item {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 6px;
  padding: 6px 14px;
  font-size: 13px;
  color: var(--wb-fg);
  border-left: 3px solid transparent;
}

.ac-item.pass {
  border-left-color: oklch(55% 0.1 155);
}

.ac-item.fail {
  border-left-color: oklch(55% 0.14 20);
  background: color-mix(in srgb, oklch(55% 0.14 20) 10%, transparent);
}

.ac-item.compromised {
  border-left-color: oklch(70% 0.14 70);
  background: color-mix(in srgb, oklch(70% 0.14 70) 12%, transparent);
}

.ac-item.unverified {
  border-left-color: oklch(60% 0.02 250);
  background: color-mix(in srgb, oklch(60% 0.02 250) 10%, transparent);
}

.ac-item.not_applicable {
  border-left-color: var(--wb-border);
  opacity: 0.7;
}

.ac-item.pending {
  border-left-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
}

.ac-status {
  flex-shrink: 0;
  font-size: 14px;
  width: 24px;
  text-align: center;
}

.ac-label {
  flex: 1;
  min-width: 0;
  line-height: 1.4;
}

.ac-verify {
  width: 100%;
  padding: 2px 0 0 30px;
  font-size: 11px;
  color: var(--wb-muted);
  line-height: 1.4;
  font-style: italic;
}

.ac-reason {
  width: 100%;
  padding: 4px 0 0 30px;
  font-size: 12px;
  color: var(--wb-muted);
  line-height: 1.4;
}

.ac-reason.ac-warn {
  color: oklch(52% 0.14 70);
  font-weight: 600;
}

.ac-reason.ac-muted {
  font-style: italic;
}

/* 文件结构 */
.file-tree {
  padding: 0 14px;
}

.file-tree-node {
  margin-bottom: 6px;
}

.file-tree-folder {
  font-size: 13px;
  font-weight: 600;
  color: var(--wb-fg);
  padding: 2px 0;
}

.file-tree-file {
  font-size: 12px;
  color: var(--wb-muted);
  padding: 1px 0 1px 0;
}

.spec-empty {
  padding: 24px 14px;
  text-align: center;
  font-size: 13px;
  color: var(--wb-muted);
}

/* ---- Evaluator 结果 ---- */

.evaluator-section {
  border-top: 1px solid var(--wb-border);
  margin-top: 8px;
  padding: 8px 0;
}

.evaluator-header {
  padding: 8px 14px 4px;
  font-size: 14px;
  font-weight: 600;
  color: var(--wb-fg);
}

.evaluator-summary {
  padding: 0 14px 8px;
  font-size: 12px;
  color: var(--wb-muted);
  line-height: 1.4;
}

.evaluator-scores {
  padding: 0 14px 8px;
}

.score-item {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  font-size: 11px;
}

.score-dim {
  width: 80px;
  color: var(--wb-muted);
  text-transform: capitalize;
  flex-shrink: 0;
}

.score-bar-bg {
  flex: 1;
  height: 6px;
  background: var(--wb-border);
  border-radius: 3px;
  overflow: hidden;
}

.score-bar-fill {
  height: 100%;
  border-radius: 3px;
  display: block;
  transition: width 0.3s ease;
}

.score-bar-fill.good {
  background: oklch(55% 0.1 155);
}

.score-bar-fill.medium {
  background: oklch(65% 0.12 85);
}

.score-bar-fill.bad {
  background: oklch(50% 0.2 25);
}

.score-val {
  width: 32px;
  text-align: right;
  color: var(--wb-fg);
  font-weight: 600;
  flex-shrink: 0;
}

.evaluator-findings {
  padding: 0 14px 8px;
}

.findings-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--wb-muted);
  margin-bottom: 4px;
}

.finding-item {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 4px 8px;
  margin-bottom: 4px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.4;
}

.finding-item.critical {
  background: color-mix(in srgb, oklch(50% 0.2 25) 10%, transparent);
  border-left: 3px solid oklch(50% 0.2 25);
}

.finding-item.major {
  background: color-mix(in srgb, oklch(60% 0.15 60) 12%, transparent);
  border-left: 3px solid oklch(60% 0.15 60);
}

.finding-item.minor {
  background: color-mix(in srgb, oklch(70% 0.08 100) 12%, transparent);
  border-left: 3px solid oklch(70% 0.08 100);
}

.finding-severity {
  flex-shrink: 0;
  font-size: 12px;
}

.finding-desc {
  flex: 1;
  min-width: 0;
  color: var(--wb-fg);
}

.finding-suggestion {
  width: 100%;
  padding-left: 20px;
  font-size: 11px;
  color: var(--wb-muted);
}
</style>
