<template>
  <form class="register-form" @submit.prevent="handleSubmit">
    <div class="form-group">
      <label for="reg-username" class="sr-only">用户名</label>
      <input
        id="reg-username"
        v-model="username"
        type="text"
        class="input-field"
        placeholder="用户名（至少3位）"
        required
        minlength="3"
        aria-label="用户名"
      />
    </div>
    <div class="form-group">
      <label for="reg-password" class="sr-only">密码</label>
      <input
        id="reg-password"
        v-model="password"
        type="password"
        class="input-field"
        placeholder="密码（至少6位）"
        required
        minlength="6"
        aria-label="密码"
      />
    </div>
    <div class="form-group">
      <label for="reg-confirm" class="sr-only">确认密码</label>
      <input
        id="reg-confirm"
        v-model="confirmPassword"
        type="password"
        class="input-field"
        placeholder="确认密码"
        required
        aria-label="确认密码"
      />
    </div>
    <div class="form-group">
      <label for="reg-invite" class="sr-only">邀请码</label>
      <input
        id="reg-invite"
        v-model="inviteCode"
        type="text"
        class="input-field invite-input"
        placeholder="邀请码（必填）"
        required
        aria-label="邀请码"
      />
      <button
        type="button"
        class="invite-request-link"
        @click="showInviteDialog = true"
      >没有邀请码？申请一个</button>
    </div>
    <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
    <div v-if="successMsg" class="form-success">{{ successMsg }}</div>
    <button
      type="submit"
      class="btn-primary register-btn"
      :disabled="loading"
    >
      {{ loading ? '注册中…' : '注册' }}
    </button>

    <InviteRequestDialog
      :show="showInviteDialog"
      @close="showInviteDialog = false"
    />
  </form>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import InviteRequestDialog from '@/components/auth/InviteRequestDialog.vue'

const emit = defineEmits<{
  success: []
}>()

const authStore = useAuthStore()
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const inviteCode = ref('')
const showInviteDialog = ref(false)
const loading = ref(false)
const errorMsg = ref('')
const successMsg = ref('')

async function handleSubmit() {
  errorMsg.value = ''
  successMsg.value = ''

  if (!username.value.trim() || !password.value || !confirmPassword.value) {
    errorMsg.value = '请填写所有字段'
    return
  }

  if (username.value.trim().length < 3) {
    errorMsg.value = '用户名至少需要3位'
    return
  }

  if (password.value.length < 6) {
    errorMsg.value = '密码至少需要6位'
    return
  }

  if (password.value !== confirmPassword.value) {
    errorMsg.value = '两次密码不一致'
    return
  }

  if (!inviteCode.value.trim()) {
    errorMsg.value = '请填写邀请码'
    return
  }

  loading.value = true

  try {
    await authStore.register(username.value.trim(), password.value, inviteCode.value.trim())
    successMsg.value = '注册成功！请切换到登录页'
    emit('success')
  } catch (err: any) {
    errorMsg.value = err.message || '注册失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.register-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.register-btn {
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

.form-success {
  font-size: 12px;
  color: oklch(40% 0.12 155);
  text-align: center;
  padding: 4px 0;
}

.form-group {
  position: relative;
}

.invite-input {
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.invite-request-link {
  margin-top: 6px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--accent);
  font-size: 12px;
  font-family: var(--font-body);
  cursor: pointer;
}

.invite-request-link:hover {
  text-decoration: underline;
}
</style>
