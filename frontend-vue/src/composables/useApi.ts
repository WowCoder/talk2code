import { useAuthStore } from '@/stores/auth'

export function useApi() {
  async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
    const authStore = useAuthStore()
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...authStore.getAuthHeaders(),
      ...(options.headers as Record<string, string> || {}),
    }

    // Remove Content-Type if body is FormData or undefined
    if (!options.body || options.body instanceof FormData) {
      delete headers['Content-Type']
    }

    const response = await fetch(url, { ...options, headers })

    if (!response.ok) {
      // Handle 401 - redirect to login
      if (response.status === 401) {
        authStore.logout()
        window.location.href = '/login'
        throw new Error('未登录或登录已过期')
      }
      const err = await response.json().catch(() => ({ error: 'Network error' }))
      throw new Error(err.message || err.error || `HTTP ${response.status}`)
    }

    return response.json()
  }

  return { api }
}
