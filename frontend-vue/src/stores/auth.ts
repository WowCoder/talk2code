import { defineStore } from 'pinia'
import { ref } from 'vue'

// JWT 现由后端以 httpOnly cookie 下发，前端不再持有/存储 token，
// 规避 localStorage 存 token 的 XSS 窃取面。所有请求走 credentials: include。

export const useAuthStore = defineStore('auth', () => {
  const username = ref<string>('用户')
  const email = ref<string>('')
  const isAuthenticated = ref(false)
  // 演示模式：后端会强制只读（demo JWT claim + before_request 守卫），
  // 前端的 isDemo 只负责 UX —— 置灰按钮、展示引导。
  const isDemo = ref(false)

  /** 应用启动时调用：请求后端确认 cookie 是否有效（带超时，避免白屏） */
  async function initAuth(): Promise<void> {
    try {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 4000)
      const resp = await fetch('/api/user/info', {
        credentials: 'include',
        signal: controller.signal,
      })
      clearTimeout(timer)
      if (resp.ok) {
        const data = await resp.json()
        username.value = data.user?.username || localStorage.getItem('username') || '用户'
        if (data.user?.username) localStorage.setItem('username', username.value)
        email.value = data.user?.email || ''
        isAuthenticated.value = true
        isDemo.value = Boolean(data.user?.is_demo)
        return
      }
    } catch {
      // 网络异常按未登录处理
    }
    isAuthenticated.value = false
    isDemo.value = false
    username.value = localStorage.getItem('username') || '用户'
  }

  async function login(usernameInput: string, password: string): Promise<void> {
    const response = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ username: usernameInput, password }),
    })

    if (!response.ok) {
      const err = await response.json().catch(() => ({ message: '登录失败' }))
      throw new Error(err.message || err.error || '登录失败')
    }

    const data = await response.json()
    username.value = data.user?.username || usernameInput
    email.value = data.user?.email || ''
    localStorage.setItem('username', username.value)
    isAuthenticated.value = true
    isDemo.value = false
  }

  /** 进入演示模式：无需密码，后端签发只读 JWT（demo claim） */
  async function enterDemo(): Promise<void> {
    const response = await fetch('/api/demo/enter', {
      method: 'POST',
      credentials: 'include',
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ message: '演示模式暂不可用' }))
      throw new Error(err.message || err.error || '演示模式暂不可用')
    }
    const data = await response.json()
    username.value = data.user?.username || '演示帐号'
    isAuthenticated.value = true
    isDemo.value = true
  }

  async function register(usernameInput: string, password: string, inviteCode: string): Promise<void> {
    const response = await fetch('/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ username: usernameInput, password, invite_code: inviteCode }),
    })

    if (!response.ok) {
      const err = await response.json().catch(() => ({ message: '注册失败' }))
      throw new Error(err.message || err.error || '注册失败')
    }
  }

  /** 同步清除本地登录态（供 401 等场景立即使用） */
  function clearAuth() {
    isAuthenticated.value = false
    isDemo.value = false
    username.value = '用户'
    email.value = ''
    localStorage.removeItem('username')
  }

  async function logout(): Promise<void> {
    try {
      await fetch('/api/logout', { method: 'POST', credentials: 'include' })
    } catch {
      // 登出失败也继续清除本地态
    }
    clearAuth()
  }

  return {
    username,
    email,
    isAuthenticated,
    isDemo,
    initAuth,
    login,
    enterDemo,
    register,
    logout,
    clearAuth,
  }
})
