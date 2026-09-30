<template>
  <div class="input-card">
    <div class="input-card-inner">
      <textarea
        v-model="content"
        class="req-textarea"
        placeholder="做一个能在浏览器里玩的 HTML5 贪吃蛇小游戏，要求…"
        rows="4"
        @input="onInput"
        @keydown="onKeydown"
      ></textarea>
      <div class="input-actions">
        <span class="input-hint"><kbd>Ctrl</kbd> + <kbd>Enter</kbd> 快速提交</span>
        <button
          class="btn-primary"
          :disabled="!content.trim() || submitting"
          @click="submit"
        >
          {{ submitting ? '提交中…' : '生成代码' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'

const emit = defineEmits<{
  selectExample: [value: string]
}>()

const content = ref('')
const submitting = ref(false)
const router = useRouter()
const { show } = useToast()

function onInput() {
  // handled by v-model
}

function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    submit()
  }
}

async function submit() {
  const text = content.value.trim()
  if (!text) return

  // 演示模式：后端有 demo 守卫兜底，这里提前拦截给更好的引导
  if (useAuthStore().isDemo) {
    show('演示模式为只读，注册后即可创建你自己的应用', 'info')
    return
  }

  submitting.value = true
  try {
    const response = await fetch('/api/requirements', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify({ content: text }),
    })

    if (!response.ok) {
      const err = await response.json().catch(() => ({ error: '创建失败' }))
      throw new Error(err.error || '创建失败')
    }

    const data = await response.json()
    router.push(`/detail/${data.requirement.id}`)
  } catch (err: any) {
    show(err.message || '创建需求失败', 'error')
  } finally {
    submitting.value = false
  }
}

// Expose for parent to set content
defineExpose({ content })
</script>

<style scoped>
.input-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  overflow: hidden;
  position: relative;
}

.input-card::before {
  content: '';
  position: absolute;
  width: 300px;
  height: 300px;
  border-radius: 50%;
  background: radial-gradient(circle, var(--accent-soft), transparent 70%);
  top: -150px;
  right: -100px;
  pointer-events: none;
}

.input-card-inner {
  position: relative;
  padding: 20px;
}

.req-textarea {
  width: 100%;
  border: none;
  resize: none;
  font-size: 15px;
  font-family: var(--font-body);
  color: var(--fg);
  background: transparent;
  outline: none;
  line-height: 1.6;
}

.req-textarea::placeholder {
  color: var(--faint);
}

.input-hint kbd {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 5px;
  border: 1px solid var(--border);
  border-bottom-width: 2px;
  border-radius: 5px;
  background: var(--bg);
  color: var(--muted);
}

.input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

.input-hint {
  font-size: 12px;
  color: var(--muted);
}
</style>
