<template>
  <div class="spec-panel">
    <div class="spec-header">
      📋 SPEC
      <span v-if="specData?.complexity" :class="['complexity-badge', specData.complexity.toLowerCase()]">
        {{ specData.complexity }}
      </span>
    </div>
    <div v-if="specData" class="spec-body">
      <!-- 核心功能 -->
      <div v-if="specData.features?.length" class="spec-section">
        <div class="section-title">🎯 核心功能</div>
        <div class="feature-list">
          <span v-for="(f, i) in specData.features" :key="i" class="feature-tag">{{ f }}</span>
        </div>
      </div>

      <!-- 技术栈 -->
      <div v-if="techStackItems.length" class="spec-section">
        <div class="section-title">⚙️ 技术栈</div>
        <div class="tech-stack">
          <span v-for="(item, i) in techStackItems" :key="i" class="tech-badge">{{ item }}</span>
        </div>
      </div>

      <!-- 数据模型 -->
      <div v-if="specData.data_model" class="spec-section">
        <div class="section-title">🗄️ 数据模型</div>
        <div class="spec-text">{{ specData.data_model }}</div>
      </div>

      <!-- 验收条件 -->
      <div v-if="specData.acceptance_criteria?.length" class="spec-section">
        <div class="section-title">✅ 验收条件 ({{ specData.acceptance_criteria.length }})</div>
        <div class="ac-section">
          <div
            v-for="(item, i) in specData.acceptance_criteria"
            :key="i"
            :class="['ac-item', acStatus(item)]"
          >
            <span :class="['ac-status', acStatus(item)]">
              {{ acStatus(item) === 'pass' ? '✅' : acStatus(item) === 'fail' ? '❌' : '⏳' }}
            </span>
            <span class="ac-label">{{ item.id }}: {{ item.label }}</span>
            <div v-if="item.how_to_verify" class="ac-verify">验证: {{ item.how_to_verify }}</div>
            <div v-if="acStatus(item) === 'fail' && item.reason" class="ac-reason">
              {{ item.reason }}
            </div>
          </div>
        </div>
      </div>

      <!-- 文件结构 -->
      <div v-if="specData.file_structure?.length" class="spec-section">
        <div class="section-title">📁 文件结构 ({{ specData.file_structure.length }})</div>
        <div class="file-tree">
          <div
            v-for="(file, i) in specData.file_structure"
            :key="i"
            class="file-tree-file"
          >
            📄 {{ file }}
          </div>
        </div>
      </div>

      <!-- 实现注意事项 -->
      <div v-if="specData.implementation_notes" class="spec-section">
        <div class="section-title">💡 实现注意事项</div>
        <div class="spec-text">{{ specData.implementation_notes }}</div>
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

export interface AcceptanceCriterion {
  id: string
  label: string
  how_to_verify?: string
  passed: boolean | null
  reason?: string
}

export interface SpecData {
  title?: string
  features?: string[]
  acceptance_criteria?: AcceptanceCriterion[]
  file_structure?: string[]
  tech_stack?: { css?: string; storage?: string; framework?: string }
  data_model?: string
  complexity?: string
  implementation_notes?: string
}

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

function acStatus(item: AcceptanceCriterion): 'pass' | 'fail' | 'pending' {
  if (item.passed === true) return 'pass'
  if (item.passed === false) return 'fail'
  return 'pending'
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
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow-y: auto;
}

.spec-header {
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--fg);
  border-bottom: 1px solid var(--border);
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

.complexity-badge.xs,
.complexity-badge.s {
  background: oklch(55% 0.1 155);
}

.complexity-badge.m {
  background: oklch(65% 0.12 85);
}

.complexity-badge.l {
  background: oklch(50% 0.2 25);
}

.spec-body {
  padding: 0;
}

.spec-section {
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}

.spec-section:last-child {
  border-bottom: none;
}

.section-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
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
  background: oklch(97% 0.01 250 / 0.5);
  color: oklch(50% 0.1 250);
  border: 1px solid oklch(85% 0.02 250);
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
  background: oklch(97% 0.01 80 / 0.5);
  color: oklch(55% 0.1 80);
  border: 1px solid oklch(85% 0.04 80);
}

/* 数据模型 / 实现注意事项 */
.spec-text {
  padding: 0 14px;
  font-size: 12px;
  color: var(--fg);
  line-height: 1.5;
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
  color: var(--fg);
  border-left: 3px solid transparent;
}

.ac-item.pass {
  border-left-color: oklch(55% 0.1 155);
}

.ac-item.fail {
  border-left-color: oklch(55% 0.14 20);
  background: oklch(97% 0.01 20 / 0.4);
}

.ac-item.pending {
  border-left-color: var(--accent);
  background: oklch(97% 0.01 50 / 0.3);
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
  color: var(--muted);
  line-height: 1.4;
  font-style: italic;
}

.ac-reason {
  width: 100%;
  padding: 4px 0 0 30px;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.4;
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
  color: var(--fg);
  padding: 2px 0;
}

.file-tree-file {
  font-size: 12px;
  color: var(--muted);
  padding: 1px 0 1px 0;
}

.spec-empty {
  padding: 24px 14px;
  text-align: center;
  font-size: 13px;
  color: var(--muted);
}

/* ---- Evaluator 结果 ---- */

.evaluator-section {
  border-top: 1px solid var(--border);
  margin-top: 8px;
  padding: 8px 0;
}

.evaluator-header {
  padding: 8px 14px 4px;
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
}

.evaluator-summary {
  padding: 0 14px 8px;
  font-size: 12px;
  color: var(--muted);
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
  color: var(--muted);
  text-transform: capitalize;
  flex-shrink: 0;
}

.score-bar-bg {
  flex: 1;
  height: 6px;
  background: var(--border);
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
  color: var(--fg);
  font-weight: 600;
  flex-shrink: 0;
}

.evaluator-findings {
  padding: 0 14px 8px;
}

.findings-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
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
  background: oklch(96% 0.02 25 / 0.6);
  border-left: 3px solid oklch(50% 0.2 25);
}

.finding-item.major {
  background: oklch(96% 0.02 65 / 0.4);
  border-left: 3px solid oklch(60% 0.15 60);
}

.finding-item.minor {
  background: oklch(96% 0.01 50 / 0.3);
  border-left: 3px solid oklch(70% 0.08 100);
}

.finding-severity {
  flex-shrink: 0;
  font-size: 12px;
}

.finding-desc {
  flex: 1;
  min-width: 0;
  color: var(--fg);
}

.finding-suggestion {
  width: 100%;
  padding-left: 20px;
  font-size: 11px;
  color: var(--muted);
}
</style>
