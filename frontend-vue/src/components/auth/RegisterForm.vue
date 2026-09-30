<template>
  <form class="register-form" @submit.prevent="handleSubmit">
    <div class="form-group">
      <label for="reg-username" class="field-label">用户名</label>
      <input
        id="reg-username"
        v-model="username"
        type="text"
        class="input-field"
        placeholder="至少 3 位"
        required
        minlength="3"
        autocomplete="username"
        aria-label="用户名"
      />
    </div>
    <div class="form-group">
      <label for="reg-password" class="field-label">设置密码</label>
      <input
        id="reg-password"
        v-model="password"
        type="password"
        class="input-field"
        placeholder="至少 6 位"
        required
        minlength="6"
        autocomplete="new-password"
        aria-label="密码"
      />
    </div>
    <div class="form-group">
      <label for="reg-confirm" class="field-label">确认密码</label>
      <input
        id="reg-confirm"
        v-model="confirmPassword"
        type="password"
        class="input-field"
        placeholder="再输入一次"
        required
        aria-label="确认密码"
      />
    </div>
    <div class="form-group">
      <div class="field-label-row">
        <label for="reg-invite" class="field-label">邀请码</label>
        <button
          type="button"
          class="field-link"
          @click="showInviteDialog = true"
        >没有？申请内测</button>
      </div>
      <input
        id="reg-invite"
        v-model="inviteCode"
        type="text"
        class="input-field invite-input"
        placeholder="例如 T2C-2026-A1B2C3"
        required
        aria-label="邀请码"
      />
    </div>
    <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
    <div v-if="successMsg" class="form-success">{{ successMsg }}</div>
    <button
      type="submit"
      class="btn-primary register-btn"
      :disabled="loading"
    >
      {{ loading ? '注册中…' : '注册账号' }}
    </button>
    <p class="form-foot">
      已有账号？
      <button type="button" class="field-link strong" @click="$emit('switchTab', 'login')">返回登录</button>
    </p>

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
  switchTab: [tab: 'login' | 'register']
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

.form-success {
  font-size: 12px;
  color: var(--color-success);
  text-align: center;
  padding: 4px 0;
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

.invite-input {
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
</style>
