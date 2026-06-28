<template>
  <div class="pagination">
    <button class="page-btn" :disabled="currentPage <= 1" @click="$emit('update:currentPage', 1)">&laquo;</button>
    <button class="page-btn" :disabled="currentPage <= 1" @click="$emit('update:currentPage', currentPage - 1)">‹</button>
    <template v-for="p in visiblePages" :key="p">
      <span v-if="p === '...'" class="page-ellipsis">…</span>
      <button v-else :class="['page-btn', { active: p === currentPage }]" @click="$emit('update:currentPage', p as number)">{{ p }}</button>
    </template>
    <button class="page-btn" :disabled="currentPage >= totalPages" @click="$emit('update:currentPage', currentPage + 1)">›</button>
    <button class="page-btn" :disabled="currentPage >= totalPages" @click="$emit('update:currentPage', totalPages)">&raquo;</button>
    <span class="page-info">{{ currentPage }} / {{ totalPages }} 页</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  currentPage: number
  totalPages: number
}>()

defineEmits<{
  'update:currentPage': [page: number]
}>()

const visiblePages = computed(() => {
  const total = props.totalPages
  const curr = props.currentPage
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1)
  }
  const pages: (number | string)[] = [1]
  if (curr > 3) pages.push('...')
  const start = Math.max(2, curr - 1)
  const end = Math.min(total - 1, curr + 1)
  for (let i = start; i <= end; i++) pages.push(i)
  if (curr < total - 2) pages.push('...')
  pages.push(total)
  return pages
})
</script>

<style scoped>
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--border);
}

.page-btn {
  min-width: 32px;
  height: 32px;
  padding: 0 6px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--fg);
  font-size: 13px;
  font-family: var(--font-body);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}

.page-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}

.page-btn.active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

.page-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.page-ellipsis {
  width: 32px;
  text-align: center;
  color: var(--muted);
  font-size: 13px;
}

.page-info {
  margin-left: 12px;
  font-size: 12px;
  color: var(--muted);
}
</style>
