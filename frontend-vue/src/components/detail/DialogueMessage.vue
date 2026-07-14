<template>
  <!-- Hidden system prompts (internal only) -->
  <template v-if="msg.hidden">
    <!-- skip rendering -->
  </template>

  <!-- System message -->
  <div v-else-if="msg.role === 'system'" class="msg system">
    {{ msg.content }}
  </div>

  <!-- User message -->
  <div v-else-if="msg.role === 'user'" class="msg user">
    <div class="bubble user-bubble">
      {{ msg.content }}
    </div>
  </div>

  <!-- Agent message (Planner/Coder/TeamLeader/PM/Architect/Engineer/QA) -->
  <div v-else-if="msg.role === 'agent' || msg.role === 'assistant'" class="msg agent">
    <div class="agent-name" :style="{ color: roleColor }">
      <span v-if="roleIcon" class="role-icon">{{ roleIcon }}</span>
      {{ displayName }}
    </div>
    <div class="bubble agent-bubble">
      {{ msg.content }}
    </div>
  </div>

  <!-- Tool call card -->
  <div v-else-if="msg.role === 'tool_call'" class="msg tool-msg">
    <ToolCallCard
      :tool-name="msg.tool_name || msg.name || ''"
      :readable="msg.readable || msg.tool_name || msg.name || ''"
      :arguments="msg.arguments"
      :pending="!msg.content && !msg.summary"
      :summary="msg.name === 'read_file' ? '' : (typeof msg.content === 'string' ? msg.content.slice(0, 200) : '')"
    />
  </div>

  <!-- Tool result — hidden (info already in ToolCallCard, avoid 3 cards per tool call) -->
  <template v-else-if="msg.role === 'tool_result'">
    <!-- skip: tool results are redundant with ToolCallCard display -->
  </template>

  <!-- Thinking — 默认折叠为一行摘要，点击展开 -->
  <div v-else-if="msg.role === 'thinking'" class="msg thinking-msg">
    <div class="thinking-toggle" @click="thinkingExpanded = !thinkingExpanded">
      <span class="thinking-toggle-icon">{{ thinkingExpanded ? '▾' : '▸' }}</span>
      <span class="agent-name" :style="{ color: roleColor }">
        <span class="role-icon">{{ roleIcon || '💭' }}</span>
        {{ displayName }}
      </span>
      <span class="thinking-label">思考中…</span>
      <span class="thinking-preview">{{ msg.content?.slice(0, 80) }}{{ (msg.content?.length || 0) > 80 ? '…' : '' }}</span>
    </div>
    <div v-if="thinkingExpanded" class="bubble agent-bubble thinking-bubble">
      {{ msg.content }}
    </div>
  </div>

  <!-- Grouped tool calls (virtual message from DialoguePanel, 兼容旧版) -->
  <div v-else-if="msg._grouped" class="msg tool-group-msg">
    <div class="tool-group-toggle" @click="toolGroupExpanded = !toolGroupExpanded">
      <span class="thinking-toggle-icon">{{ toolGroupExpanded ? '▾' : '▸' }}</span>
      <span class="tool-group-label">{{ msg.label }} ({{ msg.items?.length || 0 }} 次操作)</span>
    </div>
    <div v-if="toolGroupExpanded" class="tool-group-items">
      <template v-for="(item, j) in msg.items" :key="j">
        <ToolCallCard
          :tool-name="item.tool_name || item.name || ''"
          :readable="item.readable || item.tool_name || item.name || ''"
          :arguments="item.arguments"
          :pending="!item.content && !item.summary"
          :summary="item.name === 'read_file' ? '' : (typeof item.content === 'string' ? item.content.slice(0, 200) : '')"
        />
      </template>
    </div>
  </div>

  <!-- Iteration batch card（新版迭代分组，替代逐条 tool_call 展示）-->
  <div v-else-if="msg.role === 'iteration_batch'" class="msg iteration-msg">
    <div class="iteration-card">
      <!-- 折叠栏 -->
      <div class="iteration-toggle" @click="iterationExpanded = !iterationExpanded">
        <span class="iteration-toggle-icon">{{ iterationExpanded ? '▾' : '▸' }}</span>
        <span class="agent-name" :style="{ color: roleColor }">
          <span class="role-icon">{{ roleIcon }}</span>
          {{ displayName }}
        </span>
        <span class="iteration-label">第 {{ msg.iteration }} 轮</span>
        <span class="iteration-tool-count">{{ toolsList.length }} 个操作</span>
        <span v-if="msg.thinking_preview" class="iteration-thinking-dot" title="有思考内容">💭</span>
      </div>

      <!-- 展开内容 -->
      <div v-if="iterationExpanded" class="iteration-body">
        <!-- thinking 预览（可进一步展开） -->
        <div v-if="msg.thinking_preview" class="iteration-thinking">
          <div class="iteration-thinking-header" @click="thinkingDetailExpanded = !thinkingDetailExpanded">
            <span>{{ thinkingDetailExpanded ? '▾' : '▸' }}</span>
            <span>💭 思考中…</span>
            <span class="iteration-thinking-preview">{{ msg.thinking_preview }}</span>
          </div>
          <div v-if="thinkingDetailExpanded" class="iteration-thinking-full">
            {{ msg.thinking_preview }}
          </div>
        </div>

        <!-- agent 回复文本 -->
        <div v-if="msg.agent_text" class="iteration-agent-text">
          {{ msg.agent_text }}
        </div>

        <!-- 工具操作列表 -->
        <div class="iteration-tools">
          <div
            v-for="(tool, ti) in toolsList"
            :key="ti"
            class="iteration-tool-item"
          >
            <span class="iteration-tool-icon">{{ tool.success ? '✅' : '❌' }}</span>
            <span class="iteration-tool-label">{{ tool.readable }}</span>
            <span
              v-if="hasToolArgs(tool)"
              class="iteration-tool-detail"
              @click="toggleToolDetail(ti)"
            >{{ expandedTools.has(ti) ? '收起 ▴' : '参数 ▸' }}</span>
          </div>
          <!-- 展开的工具参数 -->
          <div v-for="ti in expandedTools" :key="'arg-' + ti" class="iteration-tool-args">
            <pre>{{ JSON.stringify(toolsList[ti]?.arguments, null, 2) }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Hook check -->
  <div v-else-if="msg.role === 'hook_check'" class="msg tool-msg">
    <HookCheckCard
      :hook-name="msg.hook_name || msg.name || 'Hook'"
      :passed="msg.passed !== false"
      :message="msg.message || msg.content"
    />
  </div>

  <!-- Default: agent-like -->
  <div v-else class="msg agent">
    <div class="agent-name" :style="{ color: roleColor }">
      <span v-if="roleIcon" class="role-icon">{{ roleIcon }}</span>
      {{ displayName }}
    </div>
    <div class="bubble agent-bubble">
      {{ msg.content }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { DialogueMessage as DialogueMessageType } from '@/types/api'
import ToolCallCard from './ToolCallCard.vue'
import HookCheckCard from './HookCheckCard.vue'

const props = defineProps<{
  msg: DialogueMessageType
}>()

const thinkingExpanded = ref(false)
const toolGroupExpanded = ref(false)
const iterationExpanded = ref(false)
const thinkingDetailExpanded = ref(false)
const expandedTools = ref(new Set<number>())

const toolsList = computed(() => {
  return props.msg.tools || []
})

function hasToolArgs(tool: any): boolean {
  const a = tool.arguments
  return a !== undefined && a !== null && Object.keys(a).length > 0
}

function toggleToolDetail(index: number) {
  const next = new Set(expandedTools.value)
  if (next.has(index)) {
    next.delete(index)
  } else {
    next.add(index)
  }
  expandedTools.value = next
}

// 角色 → 图标映射
const ROLE_ICONS: Record<string, string> = {
  'Leon（技术负责人）': '🎯',
  'Henry（开发工程师）': '⚙️',
  'Catherine（质量工程师）': '🔍',
}

// 角色 → 颜色映射
const ROLE_COLORS: Record<string, string> = {
  'Leon（技术负责人）': '#7c3aed',
  'Henry（开发工程师）': '#ea580c',
  'Catherine（质量工程师）': '#2563eb',
}

const roleIcon = computed(() => {
  const name = props.msg.name || ''
  return ROLE_ICONS[name] || ROLE_ICONS[name.split(' ')[0]] || ''
})

const roleColor = computed(() => {
  const name = props.msg.name || ''
  return ROLE_COLORS[name] || ROLE_COLORS[name.split(' ')[0]] || 'var(--accent)'
})

const displayName = computed(() => {
  return props.msg.name || 'AI'
})
</script>

<style scoped>
.msg {
  max-width: 88%;
}

.msg.user {
  align-self: flex-end;
}

.msg.agent {
  align-self: flex-start;
}

.msg.thinking-msg {
  max-width: 92%;
  align-self: flex-start;
}

.msg.tool-msg {
  max-width: 92%;
  align-self: flex-start;
}

.msg.tool-group-msg {
  max-width: 92%;
  align-self: flex-start;
}

.msg.system {
  align-self: center;
  font-size: 12px;
  color: var(--muted);
  background: var(--bg);
  padding: 6px 16px;
  border-radius: 100px;
}

.bubble {
  padding: 12px 16px;
  border-radius: 16px;
  font-size: 14px;
  line-height: 1.6;
}

.user-bubble {
  background: var(--accent);
  color: #fff;
  border-bottom-right-radius: 6px;
}

.agent-bubble {
  background: var(--bg);
  border: 1px solid var(--border);
  border-bottom-left-radius: 6px;
  color: var(--fg);
}

.agent-name {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 4px;
  letter-spacing: 0.02em;
}

.role-icon {
  margin-right: 3px;
  font-size: 13px;
}

.thinking-bubble {
  opacity: 0.85;
  font-style: italic;
  border-left: 2px solid var(--muted);
  margin-top: 8px;
}

/* ---- Thinking toggle ---- */
.thinking-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--bg);
  border: 1px solid var(--border);
  transition: background 0.15s;
}
.thinking-toggle:hover {
  background: var(--surface);
}
.thinking-toggle-icon {
  font-size: 12px;
  color: var(--muted);
  width: 14px;
  text-align: center;
  flex-shrink: 0;
}
.thinking-label {
  font-size: 11px;
  color: var(--muted);
  flex-shrink: 0;
}
.thinking-preview {
  font-size: 11px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

/* ---- Tool group toggle ---- */
.tool-group-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--bg);
  border: 1px solid var(--border);
  transition: background 0.15s;
}
.tool-group-toggle:hover {
  background: var(--surface);
}
.tool-group-label {
  font-size: 13px;
  color: var(--fg);
}
.tool-group-items {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-left: 20px;
  border-left: 2px solid var(--border);
}

/* ---- Iteration batch card ---- */
.msg.iteration-msg {
  max-width: 95%;
  align-self: flex-start;
}

.iteration-card {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}

.iteration-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 8px 12px;
  transition: background 0.15s;
}
.iteration-toggle:hover {
  background: var(--surface);
}

.iteration-toggle-icon {
  font-size: 12px;
  color: var(--muted);
  width: 14px;
  text-align: center;
  flex-shrink: 0;
}

.iteration-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--fg);
  background: var(--surface);
  padding: 2px 8px;
  border-radius: 4px;
}

.iteration-tool-count {
  font-size: 12px;
  color: var(--muted);
  flex: 1;
}

.iteration-thinking-dot {
  font-size: 13px;
}

.iteration-body {
  border-top: 1px solid var(--border);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.iteration-thinking {
  font-size: 12px;
}

.iteration-thinking-header {
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  color: var(--muted);
  padding: 4px 0;
}

.iteration-thinking-preview {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.iteration-thinking-full {
  margin-top: 6px;
  padding: 8px 12px;
  background: var(--surface);
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--muted);
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
  border-left: 2px solid var(--border);
}

.iteration-agent-text {
  font-size: 13px;
  line-height: 1.6;
  color: var(--fg);
  padding: 6px 10px;
  background: var(--surface);
  border-radius: 6px;
}

.iteration-tools {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.iteration-tool-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  font-size: 12px;
  border-radius: 4px;
}

.iteration-tool-item:hover {
  background: var(--surface);
}

.iteration-tool-icon {
  font-size: 13px;
  flex-shrink: 0;
}

.iteration-tool-label {
  color: var(--fg);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.iteration-tool-detail {
  font-size: 11px;
  color: var(--accent);
  cursor: pointer;
  flex-shrink: 0;
  user-select: none;
}

.iteration-tool-args {
  margin: 2px 0 4px 24px;
  padding: 6px 8px;
  background: var(--dark-bg);
  color: var(--dark-fg);
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 11px;
  white-space: pre-wrap;
  max-height: 160px;
  overflow-y: auto;
}
</style>
