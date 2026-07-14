<template>
  <div class="task-panel">
    <div class="task-header">📝 开发任务</div>
    <template v-if="tasks && tasks.length">
      <div class="task-progress">
        进度：<span v-for="(_, i) in tasks" :key="i" class="progress-char">
          {{ tasks[i].status === 'completed' ? '█' : '░' }}
        </span>
        <span class="progress-text">{{ completedCount }}/{{ tasks.length }} 完成</span>
      </div>
      <div class="task-body">
        <div
          v-for="(task, i) in tasks"
          :key="i"
          :class="['task-item', task.status]"
        >
          <span class="task-icon">📄</span>
          <span class="task-file">{{ task.file }}</span>
          <span class="task-desc">{{ task.description }}</span>
          <span :class="['task-badge', task.status]">
            {{ badgeLabel(task.status) }}
          </span>
        </div>
      </div>
    </template>
    <div v-else class="task-empty">
      等待开发任务...
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

export interface DevTask {
  file: string
  description: string
  status: 'pending' | 'in_progress' | 'completed' | 'blocked' | 'failed'
}

const props = defineProps<{
  tasks?: DevTask[] | null
}>()

const completedCount = computed(() => {
  if (!props.tasks) return 0
  return props.tasks.filter(t => t.status === 'completed').length
})

function badgeLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '待处理',
    in_progress: '进行中',
    completed: '已完成',
    blocked: '已阻止',
    failed: '失败',
  }
  return map[status] || status
}
</script>

<style scoped>
.task-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow-y: auto;
}

.task-header {
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--fg);
  border-bottom: 1px solid var(--border);
}

.task-progress {
  padding: 8px 14px 4px;
  font-size: 13px;
  color: var(--muted);
  letter-spacing: 0.05em;
}

.progress-text {
  margin-left: 6px;
  font-size: 12px;
  color: var(--muted);
}

.progress-char {
  font-size: 14px;
}

.task-body {
  padding: 4px 0 8px;
}

.task-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  font-size: 13px;
  color: var(--fg);
  border-left: 3px solid transparent;
}

.task-item.pending {
  border-left-color: var(--muted);
}

.task-item.in_progress {
  border-left-color: var(--accent);
}

.task-item.completed {
  border-left-color: oklch(55% 0.1 155);
}

.task-item.blocked {
  border-left-color: oklch(60% 0.15 60); /* orange */
}

.task-item.failed {
  border-left-color: oklch(50% 0.2 25); /* red */
}

.task-icon {
  flex-shrink: 0;
  font-size: 14px;
}

.task-file {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--accent);
  flex-shrink: 0;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-desc {
  flex: 1;
  min-width: 0;
  color: var(--fg);
  font-size: 12px;
  line-height: 1.4;
}

.task-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 6px;
  flex-shrink: 0;
}

.task-badge.pending {
  background: oklch(90% 0.01 60);
  color: var(--muted);
}

.task-badge.in_progress {
  background: var(--accent-soft);
  color: var(--accent);
  animation: pulse-badge 1.8s infinite;
}

.task-badge.completed {
  background: oklch(88% 0.06 155);
  color: oklch(38% 0.1 155);
}

.task-badge.blocked {
  background: oklch(88% 0.06 65);
  color: oklch(40% 0.12 55);
}

.task-badge.failed {
  background: oklch(88% 0.05 25);
  color: oklch(38% 0.15 20);
}

.task-empty {
  padding: 24px 14px;
  text-align: center;
  font-size: 13px;
  color: var(--muted);
}

@keyframes pulse-badge {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.6;
  }
}
</style>
