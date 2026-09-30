<template>
  <div class="section">
    <h3 class="section-title">账号与安全</h3>
    <div class="form-group">
      <label class="form-label">当前密码</label>
      <input v-model="currentPassword" type="password" class="input-field" placeholder="输入当前密码"
             autocomplete="current-password" />
    </div>
    <div class="form-group">
      <label class="form-label">新密码</label>
      <input v-model="newPassword" type="password" class="input-field" placeholder="输入新密码（至少6位）"
             autocomplete="new-password" />
    </div>
    <div class="form-group">
      <label class="form-label">确认新密码</label>
      <input v-model="confirmPassword" type="password" class="input-field" placeholder="再次输入新密码"
             autocomplete="new-password" @keyup.enter="onChangePassword" />
    </div>
    <button class="btn-primary" :disabled="saving" @click="onChangePassword">
      {{ saving ? '保存中…' : '更新密码' }}
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useToast } from '@/composables/useToast'
import { useApi } from '@/composables/useApi'

const { show } = useToast()
const { api } = useApi()

const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const saving = ref(false)

function clearForm() {
  currentPassword.value = ''
  newPassword.value = ''
  confirmPassword.value = ''
}

async function onChangePassword() {
  if (saving.value) return

  // 前端先做形态校验，后端会再验一次（前端提示更即时，后端是权威）
  if (!currentPassword.value || !newPassword.value) {
    show('请填写当前密码和新密码', 'error')
    return
  }
  if (newPassword.value.length < 6) {
    show('新密码至少 6 位', 'error')
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    show('两次输入的新密码不一致', 'error')
    return
  }
  if (newPassword.value === currentPassword.value) {
    show('新密码不能与当前密码相同', 'error')
    return
  }

  saving.value = true
  try {
    await api('/api/user/password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: currentPassword.value,
        new_password: newPassword.value,
      }),
    })
    clearForm()
    show('密码已更新', 'success')
  } catch (e) {
    // 401（旧密码错）由 useApi 统一跳登录页，这里只提示业务错误
    if (e instanceof Error && e.message !== '未登录或登录已过期') {
      show(e.message, 'error')
    }
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.section-title {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 600;
  color: var(--fg);
  margin-bottom: 20px;
}

.form-group {
  margin-bottom: 16px;
}

.form-label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--fg);
  margin-bottom: 6px;
}
</style>
