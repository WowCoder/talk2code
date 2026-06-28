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

  <!-- Agent message -->
  <div v-else-if="msg.role === 'agent'" class="msg agent">
    <div class="agent-name">{{ msg.name || 'AI' }}</div>
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

  <!-- Thinking — 普通气泡展示 -->
  <div v-else-if="msg.role === 'thinking'" class="msg agent">
    <div class="agent-name">💭 Thinking</div>
    <div class="bubble agent-bubble">
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
    <div class="agent-name">{{ msg.name || 'AI' }}</div>
    <div class="bubble agent-bubble">
      {{ msg.content }}
    </div>
  </div>
</template>

<script setup lang="ts">
import type { DialogueMessage as DialogueMessageType } from '@/types/api'
import ToolCallCard from './ToolCallCard.vue'
import HookCheckCard from './HookCheckCard.vue'

defineProps<{
  msg: DialogueMessageType
}>()
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
</style>
