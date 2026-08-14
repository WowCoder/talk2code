import { defineStore } from 'pinia'
import { ref } from 'vue'

// JWT 现由后端以 httpOnly cookie 下发，前端不再持有/存储 token，
// 规避 localStorage 存 token 的 XSS 窃取面。所有请求走 credentials: include。

export const useAuthStore = defineStore('auth', () => {
  const username = ref<string>('用户')
  const isAuthenticated = ref(false)

  function getAuthHeaders(): Record<string, string> {
    // cookie 承载鉴权，无需 Authorization 头
    return {}
  }

  /** 应用启动时调用：请求后端确认 cookie 是否有效 */
  async function initAuth(): Promise<void> {
    try {
      const resp = await fetch('/api/user/info', { credentials: 'include' })
      if (resp.ok) {
        const data = await resp.json()
        username.value = data.user?.username || localStorage.getItem('username') || '用户'
        if (data.user?.username) localStorage.setItem('username', username.value)
        isAuthenticated.value = true
        return
      }
    } catch {
      // 网络异常按未登录处理
    }
    isAuthenticated.value = false
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
    localStorage.setItem('username', username.value)
    isAuthenticated.value = true
  }

  async function register(usernameInput: string, password: string): Promise<void> {
    const response = await fetch('/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ username: usernameInput, password }),
    })

    if (!response.ok) {
      const err = await response.json().catch(() => ({ message: '注册失败' }))
      throw new Error(err.message || err.error || '注册失败')
    }
  }

  /** 同步清除本地登录态（供 401 等场景立即使用） */
  function clearAuth() {
    isAuthenticated.value = false
    username.value = '用户'
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
    isAuthenticated,
    getAuthHeaders,
    initAuth,
    login,
    register,
    logout,
    clearAuth,
  }
})
