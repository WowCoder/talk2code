<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="show" class="overlay" @click.self="$emit('cancel')">
        <div class="dialog" role="dialog" aria-modal="true" :aria-label="message">
          <div class="dialog-body">{{ message }}</div>
          <div class="dialog-actions">
            <button class="btn-ghost" @click="$emit('cancel')">取消</button>
            <button class="btn-danger" @click="$emit('confirm')">确认</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
defineProps<{
  show: boolean
  message: string
}>()

defineEmits<{
  confirm: []
  cancel: []
}>()
</script>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: oklch(0% 0 0 / 30%);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
  backdrop-filter: blur(2px);
}

.dialog {
  background: var(--surface);
  border-radius: 16px;
  padding: 24px;
  max-width: 400px;
  width: 90%;
  border: 1px solid var(--border);
}

.dialog-body {
  font-size: 14px;
  color: var(--fg);
  margin-bottom: 20px;
  line-height: 1.6;
}

.dialog-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}

.btn-ghost {
  padding: 8px 18px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--fg);
  font-size: 13px;
  font-family: var(--font-body);
  cursor: pointer;
}

.btn-danger {
  padding: 8px 18px;
  border: none;
  border-radius: 10px;
  background: oklch(55% 0.15 20);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  font-family: var(--font-body);
  cursor: pointer;
}
</style>
