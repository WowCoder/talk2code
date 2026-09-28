<template>
  <div class="login-page">
    <section class="brand-pane">
      <img src="@/assets/logo.png" alt="Talk2Code" class="brand-logo" />
      <span class="brand-tag">运营后台</span>
      <h1 class="brand-headline">让每一个想法<br />都跑起来</h1>
      <p class="brand-sub">
        在这里审批邀请码、盯住核心指标，<br />
        让社区的每一次创作都被看见。
      </p>
      <p class="brand-note">内部系统 · 请勿外传</p>
    </section>

    <section class="form-pane">
      <div class="login-card">
        <template v-if="!loggedIn">
          <div class="card-head">
            <h2 class="card-title">欢迎回来</h2>
            <p class="card-sub">管理员登录，与前台账号体系相互独立</p>
          </div>

          <form @submit.prevent="submit">
            <div class="field">
              <label class="field-label" for="admin-username">用户名</label>
              <input
                id="admin-username"
                v-model="username"
                type="text"
                class="input-field"
                placeholder="请输入管理员用户名"
                autocomplete="username"
                required
              />
            </div>

            <div class="field">
              <label class="field-label" for="admin-password">密码</label>
              <div class="password-wrap">
                <input
                  id="admin-password"
                  v-model="password"
                  :type="showPassword ? 'text' : 'password'"
                  class="input-field"
                  placeholder="请输入密码"
                  autocomplete="current-password"
                  required
                />
                <button type="button" class="eye-btn" :aria-label="showPassword ? '隐藏密码' : '显示密码'" @click="showPassword = !showPassword">
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8">
                    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
                    <circle cx="12" cy="12" r="3" />
                  </svg>
                </button>
              </div>
            </div>

            <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>

            <button type="submit" class="btn-primary login-btn" :disabled="loading">
              {{ loading ? '登录中…' : '登 录' }}
            </button>
          </form>

          <p class="card-foot">Token 有效期 2 小时，过期后需重新登录</p>
        </template>

        <template v-else>
          <div class="card-head">
            <h2 class="card-title">已登录</h2>
            <p class="card-sub">当前管理员：{{ adminName }}</p>
          </div>
          <div class="entry-actions">
            <RouterLink class="entry-link" to="/admin/metrics">进入总览</RouterLink>
            <RouterLink class="entry-link" to="/admin/invites">邀请码审批</RouterLink>
          </div>
          <button class="switch-btn" @click="switchAccount">切换账号</button>
        </template>
      </div>

      <RouterLink to="/" class="back-link">返回 Talk2Code 前台</RouterLink>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { adminFetch, setAdminSession, clearAdminSession, getAdminToken, getAdminUsername } from '@/composables/useAdmin'

const router = useRouter()
const username = ref('')
const password = ref('')
const showPassword = ref(false)
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
    router.push('/admin/metrics')
  } catch (err: any) {
    clearAdminSession()
    loggedIn.value = false
    errorMsg.value = err.message || '登录失败'
  } finally {
    loading.value = false
  }
}

function switchAccount() {
  clearAdminSession()
  loggedIn.value = false
  username.value = ''
  password.value = ''
  errorMsg.value = ''
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  background: var(--bg);
}

/* ===== 左：品牌区 ===== */
.brand-pane {
  width: 44%;
  max-width: 660px;
  padding: 80px 64px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 16px;
  background: linear-gradient(145deg, oklch(99% 0.012 70) 0%, oklch(93% 0.045 55) 100%);
  border-right: 1px solid var(--border);
}

.brand-logo {
  width: 56px;
  height: 56px;
  border-radius: 12px;
  margin-bottom: 8px;
}

.brand-tag {
  padding: 5px 12px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  font-weight: 600;
}

.brand-headline {
  font-family: var(--font-display);
  font-size: 40px;
  line-height: 1.35;
  font-weight: 700;
  color: var(--fg);
  margin-top: 8px;
}

.brand-sub {
  font-size: 15px;
  line-height: 1.7;
  color: var(--muted);
}

.brand-note {
  margin-top: 24px;
  font-size: 12px;
  color: oklch(65% 0.01 70);
}

/* ===== 右：表单区 ===== */
.form-pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 20px;
  padding: 40px 24px;
}

.login-card {
  width: 400px;
  max-width: 100%;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 36px;
  display: flex;
  flex-direction: column;
  gap: 22px;
  box-shadow: 0 8px 24px oklch(45% 0.06 40 / 8%);
}

.card-head {
  text-align: center;
}

.card-title {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 600;
  color: var(--fg);
}

.card-sub {
  font-size: 13px;
  color: var(--muted);
  margin-top: 4px;
}

form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--fg);
}

.password-wrap {
  position: relative;
}

.eye-btn {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  border: none;
  background: none;
  color: var(--muted);
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
  display: flex;
  transition: color 0.15s;
}

.eye-btn:hover {
  color: var(--fg);
}

.login-btn {
  width: 100%;
  padding: 12px;
  letter-spacing: 0.25em;
  margin-top: 6px;
}

.form-error {
  font-size: 12px;
  color: oklch(50% 0.18 25);
}

.card-foot {
  font-size: 12px;
  color: var(--muted);
  text-align: center;
}

.entry-actions {
  display: flex;
  gap: 10px;
}

.entry-link {
  flex: 1;
  text-align: center;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
  color: var(--fg);
  font-size: 14px;
  font-weight: 500;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}

.entry-link:hover {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}

.switch-btn {
  border: none;
  background: none;
  color: var(--muted);
  font-size: 12px;
  font-family: var(--font-body);
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.switch-btn:hover {
  color: var(--fg);
}

.back-link {
  font-size: 13px;
  font-weight: 500;
  color: var(--accent);
  transition: opacity 0.15s;
}

.back-link:hover {
  opacity: 0.75;
}

@media (max-width: 900px) {
  .login-page {
    flex-direction: column;
  }

  .brand-pane {
    width: 100%;
    max-width: none;
    padding: 40px 24px;
    border-right: none;
    border-bottom: 1px solid var(--border);
  }

  .brand-headline {
    font-size: 30px;
  }

  .login-card {
    width: 100%;
    padding: 24px;
  }
}
</style>
