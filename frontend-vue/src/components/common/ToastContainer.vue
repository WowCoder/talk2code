<template>
  <TransitionGroup name="toast" tag="div" class="toast-container">
    <div
      v-for="toast in toasts"
      :key="toast.id"
      :class="['toast', toast.type]"
    >
      {{ toast.message }}
    </div>
  </TransitionGroup>
</template>

<script setup lang="ts">
import { useToast } from '@/composables/useToast'

const { toasts } = useToast()
</script>

<style scoped>
.toast-container {
  position: fixed;
  top: 80px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 8px;
  pointer-events: none;
}

.toast {
  padding: 10px 20px;
  border-radius: 10px;
  font-size: 13px;
  font-family: var(--font-body);
  white-space: nowrap;
  pointer-events: auto;
}

.toast.success {
  background: oklch(93% 0.05 155);
  color: oklch(38% 0.1 155);
}

.toast.error {
  background: oklch(94% 0.03 20);
  color: oklch(42% 0.15 20);
}
</style>
