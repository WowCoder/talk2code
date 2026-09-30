<template>
  <div class="task-panel">
    <div class="task-header">
      <span>任务</span>
      <span v-if="tasks && tasks.length" class="task-count">{{ completedCount }} / {{ tasks.length }} 文件</span>
    </div>
    <template v-if="tasks && tasks.length">
      <!-- 整体进度条：一眼看到「做完了多少」，比方块字符更直观 -->
      <div class="task-progress">
        <div class="tp-track">
          <div class="tp-fill" :style="{ width: percent + '%' }"></div>
        </div>
        <span class="tp-label">{{ percent }}%</span>
      </div>
      <div class="task-body">
        <div
          v-for="(task, i) in tasks"
          :key="i"
          :class="['task-item', task.status]"
        >
          <span class="task-status-icon">
            <svg v-if="task.status === 'completed'" viewBox="0 0 24 24" width="14" height="14"
                 fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
            <span v-else-if="task.status === 'in_progress'" class="task-spinner"></span>
            <span v-else class="task-hollow"></span>
          </span>
          <span class="task-file">{{ task.file }}</span>
          <span class="task-desc">{{ task.description }}</span>
          <span :class="['task-badge', task.status]">
            {{ badgeLabel(task.status) }}
          </span>
        </div>
      </div>
    </template>
    <div v-else class="task-empty">
      等待开发任务…
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

const percent = computed(() => {
  if (!props.tasks?.length) return 0
  return Math.round((completedCount.value / props.tasks.length) * 100)
})

function badgeLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '等待中',
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
  margin: 12px;
  background: var(--wb-surface);
  border: 1px solid var(--wb-border);
  border-radius: 12px;
  overflow: hidden;
}

.task-header {
  padding: 11px 16px;
  font-size: 13px;
  font-weight: 600;
  color: var(--wb-fg);
  border-bottom: 1px solid var(--wb-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.task-count {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--wb-muted);
  font-variant-numeric: tabular-nums;
}

/* 整体进度条 */
.task-progress {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px 4px;
}

.tp-track {
  flex: 1;
  height: 5px;
  border-radius: 999px;
  background: var(--wb-elevated);
  overflow: hidden;
}

.tp-fill {
  height: 100%;
  border-radius: 999px;
  background: var(--accent);
  transition: width 0.4s ease;
}

.tp-label {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--wb-muted);
  font-variant-numeric: tabular-nums;
}

.task-body {
  padding: 8px 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.task-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  font-size: 13px;
  color: var(--wb-fg);
  border-radius: 8px;
}

.task-item.in_progress {
  background: rgba(207, 106, 95, 0.09);
}

/* 状态图标 */
.task-status-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.task-item.completed .task-status-icon {
  color: #7fbf8c;
}

.task-spinner {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid var(--wb-border);
  border-top-color: var(--accent);
  animation: task-spin 0.9s linear infinite;
}

@keyframes task-spin {
  to { transform: rotate(360deg); }
}

.task-hollow {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 1.5px solid var(--wb-border);
}

.task-file {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--wb-fg);
  font-weight: 600;
  flex-shrink: 0;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-desc {
  flex: 1;
  min-width: 0;
  color: var(--wb-muted);
  font-size: 12px;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 999px;
  flex-shrink: 0;
}

.task-badge.pending {
  background: var(--wb-elevated);
  color: var(--wb-muted);
}

.task-badge.in_progress {
  background: rgba(207, 106, 95, 0.16);
  color: #e08a80;
}

.task-badge.completed {
  background: rgba(76, 138, 90, 0.18);
  color: #7fbf8c;
}

.task-badge.blocked {
  background: rgba(185, 138, 46, 0.18);
  color: #d8b46a;
}

.task-badge.failed {
  background: rgba(192, 84, 74, 0.18);
  color: #e08a80;
}

.task-empty {
  padding: 24px 14px;
  text-align: center;
  font-size: 13px;
  color: var(--wb-muted);
}
</style>
