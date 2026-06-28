import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>('')
  const username = ref<string>('用户')

  const isAuthenticated = computed(() => !!token.value)

  function decodeJWT(tok: string): string {
    try {
      const payload = JSON.parse(atob(tok.split('.')[1]))
      // sub is user ID, not username — use stored username instead
      return localStorage.getItem('username') || '用户'
    } catch {
      return '用户'
    }
  }

  function getAuthHeaders(): Record<string, string> {
    if (token.value) {
      return { Authorization: `Bearer ${token.value}` }
    }
    return {}
  }

  function initFromStorage() {
    const stored = localStorage.getItem('token')
    if (stored) {
      token.value = stored
      username.value = localStorage.getItem('username') || decodeJWT(stored)
    }
  }

  async function login(usernameInput: string, password: string): Promise<void> {
    const response = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: usernameInput, password }),
    })

    if (!response.ok) {
      const err = await response.json().catch(() => ({ message: '登录失败' }))
      throw new Error(err.message || err.error || '登录失败')
    }

    const data = await response.json()
    token.value = data.token
    // 优先使用登录响应的 username，其次用输入的用户名
    username.value = data.user?.username || usernameInput
    localStorage.setItem('token', data.token)
    localStorage.setItem('username', username.value)
  }

  async function register(usernameInput: string, password: string): Promise<void> {
    const response = await fetch('/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: usernameInput, password }),
    })

    if (!response.ok) {
      const err = await response.json().catch(() => ({ message: '注册失败' }))
      throw new Error(err.message || err.error || '注册失败')
    }
  }

  function logout() {
    token.value = ''
    username.value = '用户'
    localStorage.removeItem('token')
    localStorage.removeItem('username')
  }

  return {
    token,
    username,
    isAuthenticated,
    getAuthHeaders,
    initFromStorage,
    login,
    register,
    logout,
    decodeJWT,
  }
})
