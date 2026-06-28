<template>
  <form class="login-form" @submit.prevent="handleSubmit">
    <div class="form-group">
      <input
        v-model="username"
        type="text"
        class="input-field"
        placeholder="用户名"
        required
        autocomplete="username"
      />
    </div>
    <div class="form-group">
      <input
        v-model="password"
        type="password"
        class="input-field"
        placeholder="密码"
        required
        autocomplete="current-password"
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
</style>
