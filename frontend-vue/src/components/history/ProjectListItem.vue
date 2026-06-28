<template>
  <div
    :class="['project-card', { 'trash-mode': mode === 'trash', selected }]"
    @click="onCardClick"
  >
    <!-- Checkbox (shown only in multi-select mode) -->
    <Transition name="checkbox-pop">
      <label v-if="showCheckbox" class="checkbox-wrap" @click.stop>
        <input
          type="checkbox"
          :checked="selected"
          class="checkbox-input"
          @change="$emit('toggleSelect', project.id)"
        />
        <span class="checkbox-mark"></span>
      </label>
    </Transition>

    <div class="card-body">
      <div class="card-header">
        <span class="card-title">{{ project.title }}</span>
        <StatusBadge :status="project.status" />
      </div>
      <p class="card-excerpt">{{ project.content?.substring(0, 120) || '暂无描述' }}</p>
      <div class="card-meta">
        <span>{{ formatDate(project.create_time) }}</span>
        <span v-if="project.file_count !== undefined">{{ project.file_count }} 个文件</span>
        <span v-if="project.deleted_at" class="deleted-at">删除于 {{ formatDate(project.deleted_at) }}</span>
      </div>

      <!-- Normal mode: trash button -->
      <button
        v-if="mode === 'normal'"
        class="trash-btn"
        title="移入回收站"
        @click.stop="$emit('trash', project.id)"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="3 6 5 6 21 6"></polyline>
          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>
          <path d="M10 11v6"></path>
          <path d="M14 11v6"></path>
          <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"></path>
        </svg>
      </button>

      <!-- Trash mode: restore + permanent delete -->
      <div v-if="mode === 'trash'" class="trash-actions">
        <button class="restore-btn" @click.stop="$emit('restore', project.id)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="1 4 1 10 7 10"></polyline>
            <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path>
          </svg>
          恢复
        </button>
        <button class="permanent-delete-btn" @click.stop="$emit('permanentDelete', project.id)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>
            <path d="M10 11v6"></path>
            <path d="M14 11v6"></path>
          </svg>
          彻底删除
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import StatusBadge from '@/components/common/StatusBadge.vue'
import type { RequirementSummary } from '@/types/api'

const props = defineProps<{
  project: RequirementSummary & { content?: string; file_count?: number }
  mode?: 'normal' | 'trash'
  selected?: boolean
  showCheckbox?: boolean
}>()

const emit = defineEmits<{
  click: []
  trash: [id: number]
  restore: [id: number]
  permanentDelete: [id: number]
  toggleSelect: [id: number]
}>()

function onCardClick() {
  if (props.mode === 'normal') {
    emit('click')
  }
}

function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  const mins = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)

  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  if (hours < 24) return `${hours} 小时前`
  if (days < 30) return `${days} 天前`
  return d.toLocaleDateString('zh-CN')
}
</script>

<style scoped>
.project-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 18px 20px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
  position: relative;
  display: flex;
  gap: 14px;
  align-items: flex-start;
}

.project-card:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.project-card.selected {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.project-card.trash-mode {
  cursor: default;
}

.project-card.trash-mode:hover {
  border-color: var(--border);
  background: var(--surface);
}

.project-card.trash-mode.selected {
  border-color: var(--accent);
  background: var(--accent-soft);
}

/* ===== Checkbox ===== */
.checkbox-wrap {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  margin-top: 2px;
  cursor: pointer;
  position: relative;
}

.checkbox-input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

.checkbox-mark {
  display: block;
  width: 20px;
  height: 20px;
  border: 2px solid var(--border);
  border-radius: 5px;
  transition: all 0.15s;
}

.checkbox-input:checked + .checkbox-mark {
  background: var(--accent);
  border-color: var(--accent);
}

.checkbox-input:checked + .checkbox-mark::after {
  content: '';
  position: absolute;
  top: 3px;
  left: 6px;
  width: 5px;
  height: 9px;
  border: solid #fff;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}

.checkbox-wrap:hover .checkbox-mark {
  border-color: var(--accent);
}

/* ===== Card body ===== */
.card-body {
  flex: 1;
  min-width: 0;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  gap: 10px;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--fg);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-excerpt {
  font-size: 13px;
  color: var(--muted);
  margin-bottom: 10px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-meta {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--muted);
}

.deleted-at {
  color: oklch(60% 0.12 20);
}

/* ===== Trash button (normal mode) ===== */
.trash-btn {
  position: absolute;
  bottom: 14px;
  right: 16px;
  width: 32px;
  height: 32px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--muted);
  cursor: pointer;
  display: none;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
  z-index: 2;
}

.project-card:hover .trash-btn {
  display: flex;
}

.trash-btn:hover {
  border-color: oklch(60% 0.15 20);
  color: oklch(60% 0.15 20);
  background: oklch(96% 0.01 20);
}

/* ===== Trash actions (trash mode) ===== */
.trash-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

.restore-btn,
.permanent-delete-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 13px;
  font-family: var(--font-body);
  cursor: pointer;
  transition: all 0.15s;
}

.restore-btn {
  border: 1px solid var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}

.restore-btn:hover {
  background: var(--accent);
  color: #fff;
}

.permanent-delete-btn {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--muted);
}

.permanent-delete-btn:hover {
  border-color: oklch(60% 0.15 20);
  color: oklch(60% 0.15 20);
  background: oklch(96% 0.01 20);
}

/* ===== Checkbox transition ===== */
.checkbox-pop-enter-active {
  transition: all 0.2s ease;
}

.checkbox-pop-leave-active {
  transition: all 0.15s ease;
}

.checkbox-pop-enter-from,
.checkbox-pop-leave-to {
  opacity: 0;
  transform: scale(0.6);
  width: 0;
}
</style>
