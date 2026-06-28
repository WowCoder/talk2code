<template>
  <div class="section">
    <h3 class="section-title">账户安全</h3>
    <div class="form-group">
      <label class="form-label">当前密码</label>
      <input v-model="currentPassword" type="password" class="input-field" placeholder="输入当前密码" />
    </div>
    <div class="form-group">
      <label class="form-label">新密码</label>
      <input v-model="newPassword" type="password" class="input-field" placeholder="输入新密码（至少6位）" />
    </div>
    <div class="form-group">
      <label class="form-label">确认新密码</label>
      <input v-model="confirmPassword" type="password" class="input-field" placeholder="再次输入新密码" />
    </div>
    <button class="btn-primary" @click="onChangePassword">更新密码</button>

    <div class="danger-zone">
      <div class="danger-label">注销账户</div>
      <div class="danger-desc">注销后，你的所有数据将被永久删除，不可恢复。</div>
      <button class="btn-danger" @click="onDeleteAccount">注销账户</button>
    </div>

    <ConfirmDialog
      :show="showDeleteConfirm"
      message="确定要注销账户吗？此操作不可撤销，所有数据将被永久删除。"
      @confirm="confirmDelete"
      @cancel="showDeleteConfirm = false"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useToast } from '@/composables/useToast'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'

const { show } = useToast()
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const showDeleteConfirm = ref(false)

function onChangePassword() {
  show('功能将在后续版本实现', 'success')
}

function onDeleteAccount() {
  showDeleteConfirm.value = true
}

function confirmDelete() {
  showDeleteConfirm.value = false
  show('功能将在后续版本实现', 'success')
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

.danger-zone {
  margin-top: 40px;
  padding: 20px;
  border: 1px solid oklch(75% 0.05 20);
  border-radius: 12px;
  background: oklch(97% 0.015 20);
}

.danger-label {
  font-size: 14px;
  font-weight: 600;
  color: oklch(42% 0.15 20);
  margin-bottom: 6px;
}

.danger-desc {
  font-size: 13px;
  color: var(--muted);
  margin-bottom: 16px;
  line-height: 1.5;
}
</style>
