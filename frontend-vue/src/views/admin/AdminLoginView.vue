<template>
  <div class="admin-login-page">
    <div class="admin-login-card">
      <h2 class="admin-title">运营后台</h2>
      <p class="admin-sub">管理员登录（与前台账号体系相互独立）</p>

      <template v-if="!loggedIn">
        <form @submit.prevent="submit">
          <div class="form-group">
            <input v-model="username" type="text" class="input-field" placeholder="管理员用户名" required autocomplete="username" />
          </div>
          <div class="form-group">
            <input v-model="password" type="password" class="input-field" placeholder="密码" required autocomplete="current-password" />
          </div>
          <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
          <button type="submit" class="btn-primary login-btn" :disabled="loading">
            {{ loading ? '登录中…' : '登录' }}
          </button>
        </form>
      </template>

      <template v-else>
        <p class="welcome">已登录：{{ adminName }}</p>
        <div class="entry-actions">
          <RouterLink class="entry-link" to="/admin/invites">邀请码审批</RouterLink>
          <RouterLink class="entry-link" to="/admin/metrics">指标看板</RouterLink>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { adminFetch, setAdminSession, clearAdminSession, getAdminToken, getAdminUsername } from '@/composables/useAdmin'

const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)
const errorMsg = ref('')
const loggedIn = ref(Boolean(getAdminToken()))
const adminName = ref(getAdminUsername() || '')

async function submit() {
  errorMsg.value = ''
  if (!username.value.trim() || !password.value) {
    errorMsg.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  try {
    const data = await adminFetch<{ token: string; admin: { username: string } }>('/api/admin/login', {
      method: 'POST',
      body: JSON.stringify({ username: username.value.trim(), password: password.value }),
    })
    setAdminSession(data.token, data.admin.username)
    adminName.value = data.admin.username
    loggedIn.value = true
    router.push('/admin/invites')
  } catch (err: any) {
    clearAdminSession()
    loggedIn.value = false
    errorMsg.value = err.message || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.admin-login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg);
  padding: 24px;
}

.admin-login-card {
  width: 380px;
  max-width: 100%;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 28px;
}

.admin-title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 700;
  color: var(--fg);
  margin-bottom: 4px;
}

.admin-sub {
  font-size: 13px;
  color: var(--muted);
  margin-bottom: 18px;
}

.form-group {
  margin-bottom: 12px;
}

.login-btn {
  width: 100%;
  padding: 11px;
}

.form-error {
  font-size: 12px;
  color: oklch(50% 0.15 20);
  margin-bottom: 10px;
}

.welcome {
  font-size: 14px;
  color: var(--fg);
  margin-bottom: 14px;
}

.entry-actions {
  display: flex;
  gap: 10px;
}

.entry-link {
  flex: 1;
  text-align: center;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 10px;
  color: var(--fg);
  font-size: 14px;
  text-decoration: none;
  transition: all 0.15s;
}

.entry-link:hover {
  border-color: var(--accent);
  color: var(--accent);
}
</style>
