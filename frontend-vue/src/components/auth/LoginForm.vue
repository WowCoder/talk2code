<template>
  <form class="login-form" @submit.prevent="handleSubmit">
    <div class="form-group">
      <label for="login-username" class="sr-only">用户名</label>
      <input
        id="login-username"
        v-model="username"
        type="text"
        class="input-field"
        placeholder="用户名"
        required
        autocomplete="username"
        aria-label="用户名"
      />
    </div>
    <div class="form-group">
      <label for="login-password" class="sr-only">密码</label>
      <input
        id="login-password"
        v-model="password"
        type="password"
        class="input-field"
        placeholder="密码"
        required
        autocomplete="current-password"
        aria-label="密码"
      />
    </div>
    <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
    <button
      type="submit"
      class="btn-primary login-btn"
      :disabled="loading"
    >
      {{ loading ? '登录中…' : '登录' }}
    </button>
    <div class="demo-entry">
      <button
        type="button"
        class="demo-btn"
        :disabled="demoLoading"
        @click="handleDemo"
      >
        {{ demoLoading ? '进入中…' : '以演示模式进入（只读）' }}
      </button>
    </div>
  </form>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useRouter } from 'vue-router'

const emit = defineEmits<{
  success: []
}>()

const authStore = useAuthStore()
const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)
const demoLoading = ref(false)
const errorMsg = ref('')

async function handleSubmit() {
  if (!username.value.trim() || !password.value.trim()) {
    errorMsg.value = '请输入用户名和密码'
    return
  }

  loading.value = true
  errorMsg.value = ''

  try {
    await authStore.login(username.value.trim(), password.value)
    setTimeout(() => {
      router.push('/')
    }, 800)
  } catch (err: any) {
    errorMsg.value = err.message || '登录失败'
  } finally {
    loading.value = false
  }
}

async function handleDemo() {
  demoLoading.value = true
  errorMsg.value = ''
  try {
    await authStore.enterDemo()
    router.push('/')
  } catch (err: any) {
    errorMsg.value = err.message || '演示模式暂不可用'
  } finally {
    demoLoading.value = false
  }
}
</script>

<style scoped>
.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-group {
  position: relative;
}

.login-btn {
  width: 100%;
  margin-top: 4px;
  padding: 12px;
  font-size: 15px;
}

.form-error {
  font-size: 12px;
  color: oklch(50% 0.15 20);
  text-align: center;
  padding: 4px 0;
}

.demo-entry {
  text-align: center;
  margin-top: 2px;
}

.demo-btn {
  width: 100%;
  padding: 10px;
  border: 1px dashed var(--border);
  border-radius: 10px;
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  font-family: var(--font-body);
  cursor: pointer;
  transition: all 0.15s;
}

.demo-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
</style>
