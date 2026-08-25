import { useAuthStore } from '@/stores/auth'
import router from '@/router'

export function useApi() {
  async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
    const authStore = useAuthStore()
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...(options.headers as Record<string, string> || {}),
    }

    // Remove Content-Type if body is FormData or undefined
    if (!options.body || options.body instanceof FormData) {
      delete headers['Content-Type']
    }

    const response = await fetch(url, { ...options, headers, credentials: 'include' })

    if (!response.ok) {
      // Handle 401 - clear auth state and go to login (SPA 路由跳转，避免整页刷新)
      if (response.status === 401) {
        authStore.clearAuth()
        router.push('/login')
        throw new Error('未登录或登录已过期')
      }
      // Handle 429 - rate limit
      if (response.status === 429) {
        const err = await response.json().catch(() => ({}))
        const retryAfter = err.retry_after || '若干秒'
        throw new Error(`操作太频繁，请${retryAfter === 'None' ? '稍后' : retryAfter + '秒后'}再试`)
      }
      const err = await response.json().catch(() => ({ error: 'Network error' }))
      throw new Error(err.message || err.error || `HTTP ${response.status}`)
    }

    // 204 或空响应体时不解析 JSON（否则会抛错）
    if (response.status === 204) {
      return undefined as unknown as T
    }

    const text = await response.text()
    if (!text) {
      return undefined as unknown as T
    }
    return JSON.parse(text) as T
  }

  return { api }
}
