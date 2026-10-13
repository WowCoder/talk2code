<template>
  <EmptyState v-if="filtered.length === 0 && projects.length === 0" :show-create="mode !== 'trash'" />
  <div v-else-if="filtered.length === 0" class="no-results">
    {{ emptyMessage }}
  </div>
  <TransitionGroup v-else name="fade" tag="div" class="project-list">
    <ProjectListItem
      v-for="project in filtered"
      :key="project.id"
      :project="project"
      :mode="mode"
      :selected="selectedIds?.has(project.id)"
      :show-checkbox="showCheckbox"
      @click="$emit('select', project)"
      @trash="$emit('trash', $event)"
      @restore="$emit('restore', $event)"
      @permanent-delete="$emit('permanentDelete', $event)"
      @toggle-select="$emit('toggleSelect', $event)"
    />
  </TransitionGroup>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import ProjectListItem from './ProjectListItem.vue'
import EmptyState from './EmptyState.vue'
import type { RequirementSummary } from '@/types/api'

const props = defineProps<{
  projects: (RequirementSummary & { content?: string; file_count?: number })[]
  searchQuery: string
  mode?: 'normal' | 'trash'
  emptyMessage?: string
  selectedIds?: Set<number>
  showCheckbox?: boolean
}>()

defineEmits<{
  // 整条带出来：跳转要用 slug，只要 id 的话调用方还得回头查一遍列表
  select: [project: RequirementSummary & { content?: string; file_count?: number }]
  trash: [id: number]
  restore: [id: number]
  permanentDelete: [id: number]
  toggleSelect: [id: number]
}>()

const filtered = computed(() => {
  if (!props.searchQuery) return props.projects
  return props.projects.filter((p) =>
    (p.title || '').toLowerCase().includes(props.searchQuery)
  )
})
</script>

<style scoped>
.project-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.no-results {
  text-align: center;
  padding: 40px;
  color: var(--muted);
  font-size: 14px;
}
</style>
