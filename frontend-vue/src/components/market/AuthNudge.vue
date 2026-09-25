<template>
  <div class="nudge" role="dialog" aria-label="登录或注册">
    <div class="nudge-head">
      <!-- 正向表达：只说"登录就能点赞"，不说"你没登录所以不行" -->
      <strong>{{ title }}</strong>
      <button class="nudge-close" aria-label="关闭" @click="emit('close')">×</button>
    </div>

    <form class="nudge-form" @submit.prevent="submit">
      <input v-model="username" class="nudge-input" type="text" placeholder="用户名（至少 3 位）"
             autocomplete="username" />
      <input v-model="password" class="nudge-input" type="password" placeholder="密码（至少 6 位）"
             autocomplete="current-password" />
      <p v-if="err" class="nudge-err">{{ err }}</p>
      <div class="nudge-actions">
        <button class="nudge-btn ghost" type="button" @click="mode = mode === 'login' ? 'register' : 'login'">
          {{ mode === 'login' ? '没有账号？注册' : '已有账号？登录' }}
        </button>
        <button class="nudge-btn primary" type="submit" :disabled="busy">
          {{ busy ? '请稍候…' : mode === 'login' ? '登录' : '注册' }}
        </button>
      </div>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{ title?: string }>()
const emit = defineEmits<{ close: []; success: [] }>()

const authStore = useAuthStore()
const mode = ref<'login' | 'register'>('register')
const username = ref('')
const password = ref('')
const busy = ref(false)
const err = ref('')

async function submit() {
  err.value = ''
  if (username.value.trim().length < 3) { err.value = '用户名至少 3 个字符'; return }
  if (password.value.length < 6) { err.value = '密码至少 6 个字符'; return }
  busy.value = true
  try {
    if (mode.value === 'register') {
      await authStore.register(username.value.trim(), password.value)
    }
    await authStore.login(username.value.trim(), password.value)
    emit('success')
  } catch (e) {
    err.value = e instanceof Error ? e.message : '操作失败，请重试'
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.nudge {
  width: 268px;
  padding: 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
}

.nudge-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
  font-size: 13px;
  color: var(--fg);
}

.nudge-close {
  border: none;
  background: transparent;
  color: var(--muted);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
}

.nudge-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.nudge-input {
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
  color: var(--fg);
  font-size: 13px;
}

.nudge-input:focus {
  outline: none;
  border-color: var(--accent);
}

.nudge-err {
  margin: 0;
  font-size: 12px;
  color: var(--color-danger);
}

.nudge-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.nudge-btn {
  border: none;
  border-radius: 8px;
  font-size: 12px;
  padding: 7px 12px;
  cursor: pointer;
}

.nudge-btn.ghost {
  background: transparent;
  color: var(--muted);
  padding-left: 0;
}

.nudge-btn.primary {
  background: var(--accent);
  color: var(--color-on-accent);
}
</style>
