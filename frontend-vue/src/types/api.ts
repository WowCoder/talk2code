import type { SSEQuestionFormData } from './sse'

// ===== User Types =====
export interface User {
  id: number
  username: string
  create_time?: string
}

// ===== Requirement Types =====
export type RequirementStatus = 'pending' | 'processing' | 'planning' | 'interrupted' | 'finished' | 'finished_with_issues' | 'needs_user_input' | 'failed'

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
  role: 'user' | 'agent' | 'assistant' | 'system' | 'tool_call' | 'tool_result' | 'thinking' | 'hook_check' | 'iteration_batch' | 'qa_step' | 'qa_result'
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
    blocked?: boolean
    arguments?: Record<string, unknown>
  }>
  // 聚合类消息（轮次 / AC 验收）的起止时间，前端在卡片头部展示时间区间
  start_ts?: string | null
  end_ts?: string | null
  // qa_result specific：一条 = 一个验收项（含内嵌步骤，刷新后恢复成同一张卡）
  qa_result?: {
    ac_id: string
    label?: string
    status?: string
    passed?: boolean
    steps?: Array<{
      ac_id: string
      action: string
      selector?: string
      value?: string
      status?: string
      detail?: string
      timestamp?: string
    }>
    start_ts?: string | null
    end_ts?: string | null
    summary?: string
  }
  // clarification
  question_form?: SSEQuestionFormData
  status?: string
  // plan 确认卡片（用户确认开发计划后的特殊 user 消息）
  plan_confirmed?: {
    features?: string[]
    tech_stack?: { css?: string; storage?: string; framework?: string }
    file_structure?: string[]
    complexity?: string
  }
  // hidden: 内部系统提示，不展示在前端
  hidden?: boolean
  // iteration_batch 进行中标记：true 表示本轮工具操作仍在实时累积（SSE iteration_append）
  live?: boolean
  // qa_step 验收逐步操作（Catherine 在浏览器里的每一步）
  qa_step?: {
    ac_id?: string
    action: string
    selector?: string
    value?: string
    status?: 'ok' | 'fail' | 'error' | 'na'
    detail?: string
    timestamp?: string
  }
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
  preview_token?: string
  /** 失败原因（status='failed' 时后端填充）。技术性原文，展示前需转成用户能读的话 */
  error_message?: string | null
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
