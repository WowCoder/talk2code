<template>
  <div class="dialogue-input">
    <input
      v-model="message"
      type="text"
      class="chat-input"
      placeholder="继续给 AI 提要求…"
      :disabled="disabled"
      @keypress="onKeypress"
    />
    <button
      v-if="disabled"
      class="btn-stop"
      @click="onStop"
    >
      停止生成
    </button>
    <button
      v-else
      class="btn-send"
      :disabled="!message.trim()"
      @click="send"
    >
      发送
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  disabled?: boolean
}>()

const emit = defineEmits<{
  send: [message: string]
  stop: []
}>()

const message = ref('')

function onKeypress(e: KeyboardEvent) {
  if (e.key === 'Enter' && !props.disabled) {
    send()
  }
}

function send() {
  const msg = message.value.trim()
  if (!msg || props.disabled) return
  emit('send', msg)
  message.value = ''
}

function onStop() {
  emit('stop')
}
</script>

<style scoped>
.dialogue-input {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
  display: flex;
  gap: 8px;
  flex-shrink: 0;
  background: var(--surface);
}

.chat-input {
  flex: 1;
  padding: 10px 14px;
  border: 1px solid rgba(207, 106, 95, 0.35);
  border-radius: 12px;
  font-size: 14px;
  font-family: var(--font-body);
  color: var(--fg);
  background: var(--bg);
  outline: none;
  transition: border-color 0.2s;
}

.chat-input:focus {
  border-color: var(--accent);
  box-shadow: var(--focus-ring);
}

.chat-input::placeholder {
  color: var(--faint);
}

.btn-send {
  padding: 10px 20px;
  border: none;
  border-radius: 12px;
  background: var(--accent);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-body);
  cursor: pointer;
  transition: background 0.2s;
  white-space: nowrap;
}

.btn-send:hover:not(:disabled) {
  background: var(--accent-hover);
}

.btn-send:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-stop {
  padding: 10px 20px;
  border: 1px solid var(--color-danger);
  border-radius: 12px;
  background: transparent;
  color: var(--color-danger);
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-body);
  cursor: pointer;
  transition: background 0.2s, color 0.2s;
  white-space: nowrap;
}

.btn-stop:hover {
  background: var(--color-danger);
  color: #fff;
}
</style>
