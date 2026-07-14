<template>
  <div class="dialogue-input">
    <input
      v-model="message"
      type="text"
      class="chat-input"
      placeholder="输入消息继续对话…"
      :disabled="disabled"
      @keypress="onKeypress"
    />
    <button
      v-if="disabled"
      class="btn-stop"
      @click="onStop"
    >
      ⏹ 停止
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
  border: 1px solid var(--border);
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
}

.chat-input::placeholder {
  color: oklch(65% 0.01 70);
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
  background: oklch(58% 0.13 28);
}

.btn-send:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-stop {
  padding: 10px 20px;
  border: 1px solid oklch(55% 0.15 20);
  border-radius: 12px;
  background: transparent;
  color: oklch(55% 0.15 20);
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-body);
  cursor: pointer;
  transition: background 0.2s, color 0.2s;
  white-space: nowrap;
}

.btn-stop:hover {
  background: oklch(55% 0.15 20);
  color: #fff;
}
</style>
