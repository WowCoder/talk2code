<template>
  <button
    class="btn-primary"
    :disabled="disabled || loading"
    @click="$emit('click')"
  >
    <slot v-if="!loading" />
    <span v-else>{{ loadingText }}</span>
  </button>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  disabled?: boolean
  loading?: boolean
  loadingText?: string
}>(), {
  disabled: false,
  loading: false,
  loadingText: '提交中…',
})

defineEmits<{
  click: []
}>()
</script>

<style scoped>
.btn-primary {
  padding: 10px 20px;
  border: none;
  border-radius: 12px;
  background: var(--accent);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-body);
  cursor: pointer;
  transition: background 0.2s, opacity 0.2s;
  white-space: nowrap;
}

.btn-primary:hover:not(:disabled) {
  background: oklch(58% 0.13 28);
}

.btn-primary:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
