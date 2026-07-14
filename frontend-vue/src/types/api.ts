import type { SSEQuestionFormData } from './sse'

// ===== User Types =====
export interface User {
  id: number
  username: string
  create_time?: string
}

// ===== Requirement Types =====
export type RequirementStatus = 'pending' | 'processing' | 'planning' | 'finished' | 'failed'

export interface RequirementSummary {
  id: number
  title: string
  status: RequirementStatus
  create_time: string
  is_deleted?: boolean
  deleted_at?: string | null
}

export interface CodeFile {
  filename: string
  content: string
  status?: 'modified' | 'original'
  total_lines?: number
}

export interface DialogueMessage {
  role: 'user' | 'agent' | 'assistant' | 'system' | 'tool_call' | 'tool_result' | 'thinking' | 'hook_check' | 'iteration_batch'
  name?: string
  content: string
  timestamp?: string
  // tool_call specific
  tool_name?: string
  arguments?: Record<string, unknown>
  readable?: string
  // tool_result specific
  success?: boolean
  summary?: string
  error?: string
  // hook_check specific
  passed?: boolean
  message?: string
  hook_name?: string
  // thinking specific
  // (uses content)
  // iteration_batch specific
  iteration?: number
  thinking_preview?: string
  agent_text?: string
  tools?: Array<{
    name: string
    readable: string
    success: boolean
    arguments?: Record<string, unknown>
  }>
  // clarification
  question_form?: SSEQuestionFormData
  status?: string
  // hidden: 内部系统提示，不展示在前端
  hidden?: boolean
  // grouped tool_calls (virtual message, 前端旧版兼容)
  _grouped?: boolean
  label?: string
  items?: DialogueMessage[]
}

export interface Requirement {
  id: number
  title: string
  content: string
  status: RequirementStatus
  dialogue_history: DialogueMessage[]
  code_files: CodeFile[]
  create_time: string
  update_time: string
}

// ===== API Request Types =====
export interface LoginRequest {
  username: string
  password: string
}

export interface RegisterRequest {
  username: string
  password: string
}

export interface CreateRequirementRequest {
  content: string
}

export interface ChatRequest {
  message: string
}

export interface ClarifyRequest {
  answers: Record<string, string>
}

export interface SaveCodeRequest {
  filename: string
  content: string
}

export interface SaveAllCodeRequest {
  code_files: CodeFile[]
}

export interface PermissionRequest {
  decision: 'allow' | 'deny'
}

// ===== API Response Types =====
export interface LoginResponse {
  message: string
  token: string
  user: User
}

export interface RegisterResponse {
  message: string
  user: User
}

export interface CreateRequirementResponse {
  message: string
  requirement: {
    id: number
    title: string
    status: RequirementStatus
  }
}

export interface RequirementListResponse {
  requirements: RequirementSummary[]
}

export interface RequirementDetailResponse {
  requirement: Requirement
}

export interface ChatResponse {
  message: string
  code_files: CodeFile[]
  dialogue_history: DialogueMessage[]
  updated_files: string[]
}

export interface SaveCodeResponse {
  message: string
  filename: string
  code_files: CodeFile[]
}

export interface PermissionResponse {
  status: string
  decision: string
}

export interface UserInfoResponse {
  user: User
}

export interface HealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy'
  checks: Record<string, unknown>
  version: string
  timestamp: string
}
