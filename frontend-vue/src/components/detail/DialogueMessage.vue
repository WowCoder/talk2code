<template>
  <!-- System message -->
  <div v-if="msg.role === 'system'" class="msg system">
    {{ msg.content }}
  </div>

  <!-- User message -->
  <div v-else-if="msg.role === 'user'" class="msg user">
    <div class="bubble user-bubble">
      {{ msg.content }}
    </div>
  </div>

  <!-- Agent message (Planner/Coder/TeamLeader/PM/Architect/Engineer/QA) -->
  <div v-else-if="msg.role === 'agent'" class="msg agent">
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

  <!-- Tool result -->
  <div v-else-if="msg.role === 'tool_result'" class="msg tool-msg">
    <HookCheckCard
      :hook-name="msg.tool_name || ''"
      :passed="msg.success !== false"
      :message="msg.summary || msg.error || msg.content"
    />
  </div>

  <!-- Thinking — 显示实际角色名，不再是通用的"Thinking" -->
  <div v-else-if="msg.role === 'thinking'" class="msg agent">
    <div class="agent-name" :style="{ color: roleColor }">
      <span class="role-icon">{{ roleIcon || '💭' }}</span>
      {{ displayName }}
    </div>
    <div class="bubble agent-bubble thinking-bubble">
      {{ msg.content }}
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
import { computed } from 'vue'
import type { DialogueMessage as DialogueMessageType } from '@/types/api'
import ToolCallCard from './ToolCallCard.vue'
import HookCheckCard from './HookCheckCard.vue'

const props = defineProps<{
  msg: DialogueMessageType
}>()

// 角色 → 图标映射（仅实际 Agent）
const ROLE_ICONS: Record<string, string> = {
  'TeamLeader': '🎯',   'Mike': '🎯',
  'ProductManager': '📋', 'Alice': '📋',
  'Architect': '🏗️',    'Bob': '🏗️',
  'FrontendEngineer': '⚙️', 'Alex': '⚙️',
  'QAReviewer': '🔍',   'David': '🔍',
}

// 角色 → 颜色映射（仅实际 Agent）
const ROLE_COLORS: Record<string, string> = {
  'TeamLeader': '#7c3aed',   'Mike': '#7c3aed',
  'ProductManager': '#2563eb', 'Alice': '#2563eb',
  'Architect': '#059669',    'Bob': '#059669',
  'FrontendEngineer': '#ea580c', 'Alex': '#ea580c',
  'QAReviewer': '#dc2626',   'David': '#dc2626',
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

.msg.tool-msg {
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
}
</style>
