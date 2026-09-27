<template>
  <span class="rm-inline">
    <template v-for="(t, ti) in tokens" :key="ti">
      <strong v-if="t.type === 'bold'">{{ t.value }}</strong>
      <code v-else-if="t.type === 'code'" class="rm-code">{{ t.value }}</code>
      <template v-else>{{ t.value }}</template>
    </template>
  </span>
</template>

<script setup lang="ts">
export interface InlineToken {
  type: 'text' | 'bold' | 'code'
  value: string
}

defineProps<{
  tokens: InlineToken[]
}>()
</script>

<style scoped>
.rm-code {
  background: var(--surface, #f1f5f9);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 5px;
  font-family: var(--font-mono, monospace);
  font-size: 0.92em;
  color: var(--code-fg, #be123c);
}
</style>
