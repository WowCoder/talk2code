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

  <!-- Grouped tool calls (virtual message from DialoguePanel) -->
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

// 角色 → 图标映射（按中文角色名）
const ROLE_ICONS: Record<string, string> = {
  'Leon（负责人）': '🎯',  'TeamLeader': '🎯',
  'Catherine（产品经理）': '📋', 'ProductManager': '📋',
  'Bob（架构师）': '🏗️', 'Architect': '🏗️',
  'Henry（开发）': '⚙️', 'FrontendEngineer': '⚙️',
  'Annie（测试）': '🔍', 'QAReviewer': '🔍',
  'Eve（代码审查）': '🔎', 'CodeReviewer': '🔎',
}

// 角色 → 颜色映射（按中文角色名）
const ROLE_COLORS: Record<string, string> = {
  'Leon（负责人）': '#7c3aed',  'TeamLeader': '#7c3aed',
  'Catherine（产品经理）': '#2563eb', 'ProductManager': '#2563eb',
  'Bob（架构师）': '#059669', 'Architect': '#059669',
  'Henry（开发）': '#ea580c', 'FrontendEngineer': '#ea580c',
  'Annie（测试）': '#dc2626', 'QAReviewer': '#dc2626',
  'Eve（代码审查）': '#8b5cf6', 'CodeReviewer': '#8b5cf6',
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
</style>
