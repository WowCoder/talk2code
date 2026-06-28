<template>
  <div :class="['tool-card', { pending }]">
    <div :class="['tc-icon', iconClass]">{{ icon }}</div>
    <div class="tc-body">
      <div class="tc-title">{{ readable }}</div>
      <div v-if="!pending && summary" class="tc-summary">{{ summary }}</div>
    </div>
    <!-- 详情按钮：有非空 args 才可展开 -->
    <span
      v-if="hasArgs"
      class="tc-expand"
      @click="expanded = !expanded"
    >
      {{ expanded ? '详情 ▾' : '详情 ▸' }}
    </span>
    <div v-if="expanded && hasArgs" :class="['tc-detail', { open: expanded }]">
      <pre>{{ JSON.stringify(args, null, 2) }}</pre>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'

const props = withDefaults(defineProps<{
  toolName: string
  readable?: string
  args?: Record<string, unknown>
  pending?: boolean
  summary?: string
}>(), {
  readable: '',
  args: () => ({}),
  pending: false,
  summary: '',
})

const expanded = ref(false)

const iconMap: Record<string, string> = {
  write_file: '📝',
  read_file: '📖',
  list_files: '📋',
  delete_file: '🗑',
  execute_code: '▶',
  validate_html: '🔍',
  lint_css: '🔍',
  lint_js: '🔍',
  search_docs: '🔎',
  fetch_cdn_library: '📦',
}

const icon = computed(() => iconMap[props.toolName] || '🔧')

// 判断是否有实际参数可展示（非空对象）
const hasArgs = computed(() => {
  const a = props.args
  return a !== undefined && a !== null && Object.keys(a).length > 0
})

// 图标样式：pending → wrench（蓝色旋转）；已完成 → check（绿色）
const iconClass = computed(() => {
  if (props.pending) return 'wrench'
  return 'check'
})
</script>

<style scoped>
.tool-card {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 8px 12px;
  margin: 4px 0;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 8px;
  position: relative;
}

.tool-card.pending {
  border-left: 3px solid var(--accent);
  animation: pulse-border 1.5s infinite;
}

.tc-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  flex-shrink: 0;
}

.tc-icon.wrench {
  background: oklch(90% 0.03 240);
  color: oklch(50% 0.12 250);
}

.tc-icon.check {
  background: oklch(90% 0.05 155);
  color: oklch(42% 0.12 155);
}

.tc-icon.error {
  background: oklch(92% 0.03 20);
  color: oklch(48% 0.15 20);
}

.tc-body {
  flex: 1;
  min-width: 0;
}

.tc-title {
  font-weight: 600;
  color: var(--fg);
}

.tc-summary {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
}

.tc-expand {
  font-size: 11px;
  color: var(--accent);
  cursor: pointer;
  user-select: none;
  flex-shrink: 0;
}

.tc-detail {
  display: none;
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  margin-top: 4px;
  padding: 8px;
  background: var(--dark-bg);
  color: var(--dark-fg);
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
  z-index: 10;
}

.tc-detail.open {
  display: block;
}
</style>
