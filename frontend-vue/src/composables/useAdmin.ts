// 后台管理员前端支持。
//
// token 存 sessionStorage（非 localStorage）：后台是低频使用面，会话级生命周期
// 足够；关掉标签页即失效，缩小 token 泄露窗口。有效期由后端签发时锁定（2h）。
//
// 安全边界：后台页面只渲染结构化数据（状态、计数、邮箱、码），绝不渲染任何
// 用户生成内容（不 innerHTML 需求正文、不加载 AI 生成的代码），XSS 面被
// 结构性消除 —— 这是「token 放 sessionStorage」这一取舍的主要补偿手段之一。

const TOKEN_KEY = 't2c_admin_token'
const ADMIN_KEY = 't2c_admin_username'

export function getAdminToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}

export function getAdminUsername(): string | null {
  return sessionStorage.getItem(ADMIN_KEY)
}

export function setAdminSession(token: string, username: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
  sessionStorage.setItem(ADMIN_KEY, username)
}

export function clearAdminSession(): void {
  sessionStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(ADMIN_KEY)
}

/** 带管理员 token 的 fetch。401 时清会话（token 过期），由调用方跳登录页。 */
export async function adminFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getAdminToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init.headers as Record<string, string>) || {}),
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const resp = await fetch(path, { ...init, headers })
  if (resp.status === 401) {
    clearAdminSession()
    throw new Error('登录已过期，请重新登录')
  }
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    throw new Error((data as { error?: string }).error || `请求失败（${resp.status}）`)
  }
  return data as T
}

export interface InviteRow {
  id: number
  code: string | null
  applicant_email: string
  applicant_phone: string
  applicant_note: string
  status: string
  delivery_status: string
  created_at: string | null
  decided_at: string | null
  expires_at: string | null
  used_at: string | null
  reject_reason: string | null
}
