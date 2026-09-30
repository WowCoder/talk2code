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

    <!-- 首字母头像：列表扫读的视觉锚点 -->
    <span class="card-avatar">{{ avatarChar }}</span>

    <div class="card-body">
      <div class="card-header">
        <span class="card-title">{{ project.title }}</span>
        <StatusBadge :status="project.status" />
      </div>
      <div class="card-meta">
        <span v-if="project.file_count !== undefined">{{ project.file_count }} 个文件</span>
        <span>{{ formatDate(project.create_time) }}</span>
        <span v-if="project.deleted_at" class="deleted-at">删除于 {{ formatDate(project.deleted_at) }}</span>
      </div>
    </div>

    <!-- Normal mode: 查看 + 移入回收站(X) -->
    <template v-if="mode === 'normal'">
      <button class="view-btn" @click.stop="$emit('click')">查看</button>
      <button
        class="trash-btn"
        title="移入回收站"
        aria-label="移入回收站"
        @click.stop="$emit('trash', project.id)"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18"/>
          <line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </template>

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
</template>

<script setup lang="ts">
import { computed } from 'vue'
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

const avatarChar = computed(() =>
  (props.project.title || '?').trim().charAt(0).toUpperCase()
)

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
  border-radius: 12px;
  padding: 14px 16px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
  position: relative;
  display: flex;
  gap: 12px;
  align-items: center;
}

.project-card:hover {
  border-color: rgba(207, 106, 95, 0.45);
}

.project-card.selected {
  border-color: rgba(207, 106, 95, 0.7);
  box-shadow: 0 0 0 2px rgba(207, 106, 95, 0.35);
}

.project-card.trash-mode {
  cursor: default;
}

.project-card.trash-mode:hover {
  border-color: var(--border);
}

.project-card.trash-mode.selected {
  border-color: rgba(207, 106, 95, 0.7);
  box-shadow: 0 0 0 2px rgba(207, 106, 95, 0.35);
}

/* ===== 首字母头像 ===== */
.card-avatar {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--accent-soft);
  color: var(--accent-strong);
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

/* ===== Checkbox ===== */
.checkbox-wrap {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
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
  gap: 10px;
  margin-bottom: 4px;
  min-width: 0;
}

.card-title {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--fg);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--faint);
}

.deleted-at {
  color: var(--color-danger);
}

/* ===== 查看 / 移除按钮 ===== */
.view-btn {
  flex-shrink: 0;
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--fg);
  font-size: 12.5px;
  font-weight: 500;
  font-family: var(--font-body);
  cursor: pointer;
  transition: all 0.15s;
}

.view-btn:hover {
  border-color: var(--accent);
  color: var(--accent-strong);
  background: var(--accent-soft);
}

.trash-btn {
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--faint);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}

.trash-btn:hover {
  color: var(--color-danger);
  background: var(--color-danger-soft);
}

/* ===== Trash actions (trash mode) ===== */
.trash-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.restore-btn,
.permanent-delete-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 12.5px;
  font-family: var(--font-body);
  cursor: pointer;
  transition: all 0.15s;
}

.restore-btn {
  border: 1px solid var(--accent);
  background: var(--accent-soft);
  color: var(--accent-strong);
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
  border-color: var(--color-danger);
  color: var(--color-danger);
  background: var(--color-danger-soft);
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
