<template>
  <div class="section">
    <h3 class="section-title">个人资料</h3>
    <div class="profile-avatar-row">
      <Avatar :username="authStore.username" size="lg" />
      <div>
        <div class="profile-name">{{ authStore.username }}</div>
        <div class="profile-hint">修改信息后点击保存</div>
      </div>
    </div>
    <div class="form-group">
      <label class="form-label">用户名</label>
      <input v-model="username" type="text" class="input-field" />
    </div>
    <div class="form-group">
      <label class="form-label">邮箱</label>
      <input v-model="email" type="email" class="input-field" placeholder="暂未设置" />
    </div>
    <button class="btn-primary" @click="onSave">保存修改</button>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useToast } from '@/composables/useToast'
import Avatar from '@/components/common/Avatar.vue'

const authStore = useAuthStore()
const { show } = useToast()
const username = ref(authStore.username)
const email = ref('')

function onSave() {
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

.profile-avatar-row {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
  padding: 16px;
  background: var(--bg);
  border-radius: 12px;
}

.profile-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--fg);
}

.profile-hint {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
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
