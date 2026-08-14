<template>
  <div class="login-page">
    <div class="login-card">
      <!-- Brand -->
      <div class="brand">
        <img src="@/assets/logo.png" alt="Talk2Code" class="brand-logo" />
        <h1 class="brand-title">
          Talk<span>2</span>Code
        </h1>
        <p class="brand-subtitle">用自然语言创造应用</p>
      </div>

      <!-- Tab Switcher -->
      <TabSwitcher v-model:activeTab="activeTab" />

      <!-- Forms -->
      <LoginForm
        v-if="activeTab === 'login'"
        @success="onLoginSuccess"
      />
      <RegisterForm
        v-else
        @success="onRegisterSuccess"
      />

      <!-- Hint -->
      <p class="hint">测试账号 <code>test</code> / <code>123456</code></p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import TabSwitcher from '@/components/auth/TabSwitcher.vue'
import LoginForm from '@/components/auth/LoginForm.vue'
import RegisterForm from '@/components/auth/RegisterForm.vue'

const activeTab = ref<'login' | 'register'>('login')

// 已登录跳转由路由守卫（router.beforeEach）处理，无需在此手动判断

function onLoginSuccess() {
  // Already handled in LoginForm (delayed redirect)
}

function onRegisterSuccess() {
  activeTab.value = 'login'
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg);
  position: relative;
  overflow: hidden;
}

/* Radial gradient overlay */
.login-page::before {
  content: '';
  position: absolute;
  width: 600px;
  height: 600px;
  border-radius: 50%;
  background: radial-gradient(circle, var(--accent-soft), transparent 70%);
  top: -200px;
  right: -200px;
  pointer-events: none;
}

.login-card {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 40px 36px;
  width: 400px;
  max-width: 90vw;
}

.brand {
  text-align: center;
  margin-bottom: 24px;
}

.brand-logo {
  width: 64px;
  height: 64px;
  border-radius: 12px;
  margin-bottom: 16px;
}

.brand-title {
  font-family: var(--font-display);
  font-size: 32px;
  font-weight: 700;
  color: var(--fg);
  letter-spacing: -0.02em;
  margin-bottom: 6px;
}

.brand-title span {
  color: var(--accent);
}

.brand-subtitle {
  font-size: 14px;
  color: var(--muted);
}

.hint {
  text-align: center;
  font-size: 12px;
  color: var(--muted);
  margin-top: 20px;
}

.hint code {
  background: var(--bg);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--accent);
  border: 1px solid var(--border);
}
</style>
