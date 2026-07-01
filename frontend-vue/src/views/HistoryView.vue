<template>
  <div class="history-page">
    <AppNav />
    <main class="history-main">
      <!-- Header row -->
      <div class="history-header">
        <div class="header-left">
          <h2 class="history-title">历史对话</h2>
          <span class="history-count">{{ totalCount }} 个项目</span>
        </div>
        <div class="header-right">
          <button
            v-if="!isMultiSelect"
            class="multi-select-toggle"
            @click="enterMultiSelect"
          >多选</button>
          <button
            v-else
            class="multi-select-toggle active"
            @click="exitMultiSelect"
          >取消</button>
        </div>
      </div>

      <!-- Tabs -->
      <div class="tabs">
        <button :class="['tab-btn', { active: activeTab === 'active' }]" @click="switchTab('active')">运行中</button>
        <button :class="['tab-btn', { active: activeTab === 'trash' }]" @click="switchTab('trash')">
          回收站
          <span v-if="trashProjects.length" class="tab-badge">{{ trashProjects.length }}</span>
        </button>
      </div>

      <!-- Select all bar (only in multi-select mode) -->
      <Transition name="slide-down">
        <div v-if="isMultiSelect && currentList.length > 0" class="select-bar">
          <label class="select-all-label">
            <input
              type="checkbox"
              :checked="isAllSelected"
              :indeterminate="isIndeterminate"
              class="select-all-checkbox"
              @change="toggleSelectAll"
            />
            <span>{{ isAllSelected ? '取消全选' : selectedCount > 0 ? `已选 ${selectedCount} 项` : '全选' }}</span>
          </label>
        </div>
      </Transition>

      <!-- Active tab -->
      <template v-if="activeTab === 'active'">
        <SearchBar @search="onSearch" />
        <ProjectList
          :projects="paginatedActive"
          :search-query="searchQuery"
          :selected-ids="selectedIds"
          :show-checkbox="isMultiSelect"
          mode="normal"
          @select="onSelect"
          @trash="onTrash"
          @toggle-select="toggleSelect"
        />
        <Pagination
          v-if="activeTotalPages > 1"
          v-model:current-page="activePage"
          :total-pages="activeTotalPages"
        />
      </template>

      <!-- Trash tab -->
      <template v-if="activeTab === 'trash'">
        <ProjectList
          :projects="paginatedTrash"
          search-query=""
          :selected-ids="selectedIds"
          :show-checkbox="isMultiSelect"
          mode="trash"
          empty-message="回收站为空"
          @restore="onRestore"
          @permanent-delete="onPermanentDelete"
          @toggle-select="toggleSelect"
        />
        <Pagination
          v-if="trashTotalPages > 1"
          v-model:current-page="trashPage"
          :total-pages="trashTotalPages"
        />
      </template>
    </main>

    <!-- Floating batch action bar -->
    <Transition name="slide-up">
      <div v-if="isMultiSelect && selectedCount > 0" class="batch-bar">
        <span class="batch-count">已选 {{ selectedCount }} 项</span>
        <div class="batch-actions">
          <button
            v-if="activeTab === 'active'"
            class="batch-btn batch-trash"
            @click="onBatchTrash"
          >移入回收站</button>
          <template v-if="activeTab === 'trash'">
            <button class="batch-btn batch-restore" @click="onBatchRestore">批量恢复</button>
            <button class="batch-btn batch-delete" @click="onBatchPermanentDelete">批量删除</button>
          </template>
        </div>
      </div>
    </Transition>

    <!-- Confirm Dialog -->
    <ConfirmDialog
      :show="confirmDialog.show"
      :message="confirmDialog.message"
      @confirm="confirmDialog.onConfirm"
      @cancel="confirmDialog.show = false"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useRequirementStore } from '@/stores/requirement'
import { useApi } from '@/composables/useApi'
import { useToast } from '@/composables/useToast'
import AppNav from '@/components/layout/AppNav.vue'
import SearchBar from '@/components/history/SearchBar.vue'
import ProjectList from '@/components/history/ProjectList.vue'
import Pagination from '@/components/history/Pagination.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import type { RequirementSummary } from '@/types/api'

const router = useRouter()
const authStore = useAuthStore()
const reqStore = useRequirementStore()
const { api } = useApi()
const { show } = useToast()

const activeTab = ref<'active' | 'trash'>('active')
const projects = ref<(RequirementSummary & { content?: string; file_count?: number })[]>([])
const trashProjects = ref<(RequirementSummary & { content?: string; file_count?: number })[]>([])
const searchQuery = ref('')
const selectedIds = ref<Set<number>>(new Set())
const isMultiSelect = ref(false)
const activePage = ref(1)
const trashPage = ref(1)
const pageSize = 10

const confirmDialog = reactive({
  show: false,
  message: '',
  onConfirm: () => {},
})

// ===== Derived =====

const filteredActive = computed(() => {
  if (!searchQuery.value) return projects.value
  return projects.value.filter(p => (p.title || '').toLowerCase().includes(searchQuery.value))
})

const currentList = computed(() => activeTab.value === 'active' ? filteredActive.value : trashProjects.value)
const totalCount = computed(() => activeTab.value === 'active' ? projects.value.length : trashProjects.value.length)

const activeTotalPages = computed(() => Math.max(1, Math.ceil(filteredActive.value.length / pageSize)))
const paginatedActive = computed(() => {
  const start = (activePage.value - 1) * pageSize
  return filteredActive.value.slice(start, start + pageSize)
})

const trashTotalPages = computed(() => Math.max(1, Math.ceil(trashProjects.value.length / pageSize)))
const paginatedTrash = computed(() => {
  const start = (trashPage.value - 1) * pageSize
  return trashProjects.value.slice(start, start + pageSize)
})

const selectedCount = computed(() => selectedIds.value.size)

const isAllSelected = computed(() => {
  const ids = currentList.value.map(p => p.id)
  return ids.length > 0 && ids.every(id => selectedIds.value.has(id))
})

const isIndeterminate = computed(() => {
  const ids = currentList.value.map(p => p.id)
  return ids.some(id => selectedIds.value.has(id)) && !isAllSelected.value
})

// ===== Lifecycle =====

watch(searchQuery, () => { activePage.value = 1 })

onMounted(() => { loadAll() })

async function loadAll() {
  await Promise.all([loadProjects(), loadTrash()])
}

async function loadProjects() {
  try {
    const data = await api<{ requirements: (RequirementSummary & { content?: string; file_count?: number })[] }>('/api/requirements')
    projects.value = data.requirements || []
  } catch (err: any) { /* 401 handled by useApi */ }
}

async function loadTrash() {
  try {
    const data = await api<{ requirements: (RequirementSummary & { content?: string; file_count?: number })[] }>('/api/requirements?trash=true')
    trashProjects.value = data.requirements || []
  } catch (err: any) { /* 401 handled by useApi */ }
}

function switchTab(tab: 'active' | 'trash') {
  activeTab.value = tab
  searchQuery.value = ''
  activePage.value = 1
  trashPage.value = 1
  exitMultiSelect()
}

function onSearch(query: string) { searchQuery.value = query }
function onSelect(id: number) { router.push(`/detail/${id}`) }

// ===== Multi-select =====

function enterMultiSelect() {
  isMultiSelect.value = true
  selectedIds.value = new Set()
}

function exitMultiSelect() {
  isMultiSelect.value = false
  selectedIds.value = new Set()
}

function toggleSelect(id: number) {
  if (!isMultiSelect.value) return
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

function toggleSelectAll() {
  if (isAllSelected.value) {
    selectedIds.value = new Set()
  } else {
    selectedIds.value = new Set(currentList.value.map(p => p.id))
  }
}

// ===== Single operations =====

function onTrash(id: number) {
  const item = projects.value.find(p => p.id === id)
  confirmDialog.message = `确定要将「${item?.title || '该项目'}」移入回收站吗？`
  confirmDialog.onConfirm = async () => {
    confirmDialog.show = false
    try {
      await reqStore.trashRequirement(id)
      show('已移入回收站', 'success')
      await loadAll()
    } catch (err: any) { show(err.message || '操作失败', 'error') }
  }
  confirmDialog.show = true
}

function onRestore(id: number) {
  const item = trashProjects.value.find(p => p.id === id)
  confirmDialog.message = `确定要恢复「${item?.title || '该项目'}」吗？`
  confirmDialog.onConfirm = async () => {
    confirmDialog.show = false
    try {
      await reqStore.restoreRequirement(id)
      show('已恢复', 'success')
      await loadAll()
    } catch (err: any) { show(err.message || '操作失败', 'error') }
  }
  confirmDialog.show = true
}

function onPermanentDelete(id: number) {
  const item = trashProjects.value.find(p => p.id === id)
  confirmDialog.message = `确定要彻底删除「${item?.title || '该项目'}」吗？此操作不可撤销。`
  confirmDialog.onConfirm = async () => {
    confirmDialog.show = false
    try {
      await reqStore.deleteRequirement(id)
      show('已彻底删除', 'success')
      await loadAll()
    } catch (err: any) { show(err.message || '操作失败', 'error') }
  }
  confirmDialog.show = true
}

// ===== Batch operations =====

async function onBatchTrash() {
  const count = selectedCount.value
  confirmDialog.message = `确定要将 ${count} 个项目移入回收站吗？`
  confirmDialog.onConfirm = async () => {
    confirmDialog.show = false
    try {
      await Promise.all([...selectedIds.value].map(id => reqStore.trashRequirement(id)))
      show(`已将 ${count} 个项目移入回收站`, 'success')
      exitMultiSelect()
      await loadAll()
    } catch (err: any) { show(err.message || '操作失败', 'error') }
  }
  confirmDialog.show = true
}

async function onBatchRestore() {
  const count = selectedCount.value
  confirmDialog.message = `确定要恢复 ${count} 个项目吗？`
  confirmDialog.onConfirm = async () => {
    confirmDialog.show = false
    try {
      await Promise.all([...selectedIds.value].map(id => reqStore.restoreRequirement(id)))
      show(`已恢复 ${count} 个项目`, 'success')
      exitMultiSelect()
      await loadAll()
    } catch (err: any) { show(err.message || '操作失败', 'error') }
  }
  confirmDialog.show = true
}

async function onBatchPermanentDelete() {
  const count = selectedCount.value
  confirmDialog.message = `确定要彻底删除 ${count} 个项目吗？此操作不可撤销。`
  confirmDialog.onConfirm = async () => {
    confirmDialog.show = false
    try {
      await Promise.all([...selectedIds.value].map(id => reqStore.deleteRequirement(id)))
      show(`已彻底删除 ${count} 个项目`, 'success')
      exitMultiSelect()
      await loadAll()
    } catch (err: any) { show(err.message || '操作失败', 'error') }
  }
  confirmDialog.show = true
}
</script>

<style scoped>
.history-page {
  min-height: 100vh;
  background: var(--bg);
  padding-bottom: 80px;
}

.history-main {
  max-width: 720px;
  margin: 0 auto;
  padding: 32px 24px;
}

/* ===== Header ===== */
.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.header-right {
  display: flex;
  align-items: center;
}

.history-title {
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 700;
  color: var(--fg);
}

.history-count {
  font-size: 13px;
  color: var(--muted);
}

/* ===== Multi-select toggle ===== */
.multi-select-toggle {
  padding: 6px 16px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--surface);
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
  font-family: var(--font-body);
  cursor: pointer;
  transition: all 0.15s;
}

.multi-select-toggle:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.multi-select-toggle.active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

/* ===== Tabs ===== */
.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 16px;
  padding: 4px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
}

.tab-btn {
  flex: 1;
  padding: 8px 16px;
  border: none;
  border-radius: 9px;
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
  font-family: var(--font-body);
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.tab-btn:hover { color: var(--fg); }
.tab-btn.active { background: var(--accent); color: #fff; }

.tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: oklch(100% 0 0 / 25%);
  font-size: 11px;
  font-weight: 600;
}

/* ===== Select all bar ===== */
.select-bar {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
  padding: 10px 14px;
  background: var(--accent-soft);
  border: 1px solid var(--accent);
  border-radius: 10px;
}

.select-all-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--accent);
  cursor: pointer;
  user-select: none;
  font-weight: 500;
}

.select-all-checkbox {
  width: 18px;
  height: 18px;
  accent-color: var(--accent);
  cursor: pointer;
}

/* ===== Floating batch bar ===== */
.batch-bar {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 12px 20px;
  display: flex;
  align-items: center;
  gap: 14px;
  box-shadow: 0 4px 24px oklch(0% 0 0 / 12%);
  z-index: 100;
}

.batch-count {
  font-size: 13px;
  color: var(--muted);
  white-space: nowrap;
}

.batch-actions {
  display: flex;
  gap: 8px;
}

.batch-btn {
  padding: 7px 16px;
  border-radius: 9px;
  font-size: 13px;
  font-family: var(--font-body);
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}

.batch-trash {
  border: 1px solid oklch(60% 0.15 20);
  background: oklch(96% 0.01 20);
  color: oklch(60% 0.15 20);
}
.batch-trash:hover { background: oklch(60% 0.15 20); color: #fff; }

.batch-restore {
  border: 1px solid var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}
.batch-restore:hover { background: var(--accent); color: #fff; }

.batch-delete {
  border: 1px solid oklch(60% 0.15 20);
  background: oklch(60% 0.15 20);
  color: #fff;
}
.batch-delete:hover { background: oklch(50% 0.15 20); border-color: oklch(50% 0.15 20); }

/* ===== Transitions ===== */
.slide-down-enter-active,
.slide-down-leave-active { transition: all 0.2s ease; }
.slide-down-enter-from,
.slide-down-leave-to { opacity: 0; transform: translateY(-8px); }

.slide-up-enter-active,
.slide-up-leave-active { transition: all 0.25s ease; }
.slide-up-enter-from,
.slide-up-leave-to { opacity: 0; transform: translateX(-50%) translateY(20px); }
</style>
