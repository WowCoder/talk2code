<template>
  <form class="login-form" @submit.prevent="handleSubmit">
    <div class="form-group">
      <label for="login-username" class="field-label">用户名</label>
      <input
        id="login-username"
        v-model="username"
        type="text"
        class="input-field"
        placeholder="你的用户名"
        required
        autocomplete="username"
        aria-label="用户名"
      />
    </div>
    <div class="form-group">
      <div class="field-label-row">
        <label for="login-password" class="field-label">密码</label>
        <button
          type="button"
          class="field-link"
          @click="showPassword = !showPassword"
        >{{ showPassword ? '隐藏' : '显示' }}</button>
      </div>
      <input
        id="login-password"
        v-model="password"
        :type="showPassword ? 'text' : 'password'"
        class="input-field"
        placeholder="••••••••"
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
    <p class="form-foot">
      还没有账号？
      <button type="button" class="field-link strong" @click="$emit('switchTab', 'register')">申请内测</button>
    </p>
    <div class="demo-entry">
      <button
        type="button"
        class="demo-btn"
        :disabled="demoLoading"
        @click="handleDemo"
      >
        {{ demoLoading ? '进入中…' : '以演示身份进入（只读，无需注册）' }}
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
  switchTab: [tab: 'login' | 'register']
}>()

const authStore = useAuthStore()
const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)
const demoLoading = ref(false)
const errorMsg = ref('')
const showPassword = ref(false)

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
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--fg);
}

.field-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.field-link {
  border: none;
  background: none;
  padding: 0;
  font-size: 12px;
  font-family: var(--font-body);
  color: var(--accent-strong);
  cursor: pointer;
}

.field-link:hover {
  text-decoration: underline;
}

.field-link.strong {
  font-weight: 600;
}

.login-btn {
  width: 100%;
  margin-top: 4px;
  padding: 12px;
  font-size: 15px;
}

.form-foot {
  text-align: center;
  font-size: 13px;
  color: var(--muted);
  margin: 0;
}

.form-error {
  font-size: 12px;
  color: var(--color-danger);
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
  color: var(--accent-strong);
  background: var(--accent-soft);
}
</style>
