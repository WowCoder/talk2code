<template>
  <div v-if="request" class="permission-card">
    <div class="perm-title">🔐 权限请求</div>
    <div class="perm-detail">
      <b>操作:</b> {{ request.tool_name || request.readable || '未知操作' }}
    </div>
    <div class="perm-reason">
      {{ request.message || request.reason || 'Agent 请求执行此操作' }}
    </div>
    <div class="perm-actions">
      <button class="btn-allow" @click="decide('allow')">允许</button>
      <button class="btn-deny" @click="decide('deny')">拒绝</button>
      <span class="perm-countdown">{{ countdown }}s 后自动拒绝</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onUnmounted } from 'vue'
import { useRequirementStore } from '@/stores/requirement'
import { useToast } from '@/composables/useToast'
import type { SSEPermissionData } from '@/types/sse'

const props = defineProps<{
  request: SSEPermissionData | null
}>()

const emit = defineEmits<{
  resolved: []
}>()

const store = useRequirementStore()
const { show } = useToast()
const countdown = ref(30)
let timer: ReturnType<typeof setInterval> | null = null

function startCountdown() {
  countdown.value = 30
  if (timer) clearInterval(timer)
  timer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      clearInterval(timer!)
      timer = null
      decide('deny')
    }
  }, 1000)
}

async function decide(decision: 'allow' | 'deny') {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
  try {
    await store.submitPermission(decision)
    emit('resolved')
  } catch (err: any) {
    show('权限提交失败', 'error')
  }
}

watch(() => props.request, (newVal) => {
  if (newVal) startCountdown()
}, { immediate: true })

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.permission-card {
  background: var(--accent-soft);
  border: 2px solid var(--accent);
  border-radius: 12px;
  padding: 16px;
  margin: 8px 0;
  animation: fadeIn 0.3s;
  align-self: flex-start;
  width: 100%;
}

.perm-title {
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 8px;
}

.perm-detail {
  font-size: 13px;
  color: var(--fg);
  margin-bottom: 4px;
}

.perm-reason {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 12px;
}

.perm-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.btn-allow {
  padding: 10px 20px;
  border: none;
  border-radius: 12px;
  background: oklch(58% 0.08 155);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-body);
  cursor: pointer;
}

.btn-deny {
  padding: 10px 20px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  font-size: 14px;
  font-family: var(--font-body);
  color: var(--fg);
  cursor: pointer;
}

.perm-countdown {
  font-size: 11px;
  color: var(--muted);
}
</style>
