// ===== SSE Event Types =====

export type SSEEventType =
  | 'connected'
  | 'dialogue'
  | 'code'
  | 'progress'
  | 'question-form'
  | 'tool_call'
  | 'tool_result'
  | 'thinking'
  | 'hook_check'
  | 'complete'
  | 'permission_request'
  | 'trace_summary'
  | 'error'
  | 'preview'

// ===== SSE Event Data Shapes =====

export interface SSEConnectedData {
  requirement_id: number
}

export interface SSEDialogueData {
  role: string
  name?: string
  content: string
  timestamp?: string
  status?: string
}

export interface SSECodeData {
  filename?: string
  content?: string
  line_number?: number
  is_complete?: boolean
  files?: Array<{
    filename: string
    content: string
  }>
}

export interface SSEProgressData {
  current_agent: string
  progress: number
  status: string
}

export interface SSEQuestionFormData {
  questions: Array<{
    id: string
    label: string
    type: 'radio' | 'text'
    options?: string[]
  }>
  /** 是否已提交（刷新页面后后端标记） */
  submitted?: boolean
  /** 已提交的答案 */
  answers?: Record<string, string>
}

export interface SSEToolCallData {
  tool_name: string
  readable?: string
  arguments?: Record<string, unknown>
}

export interface SSEToolResultData {
  tool_name: string
  success: boolean
  summary?: string
  error?: string
}

export interface SSEThinkingData {
  content: string
}

export interface SSEHookCheckData {
  hook_name: string
  passed: boolean
  message?: string
}

export interface SSECompleteData {
  requirement_id: number
  code_files?: Array<{
    filename: string
    content: string
  }>
}

export interface SSEPermissionData {
  tool_name?: string
  readable?: string
  message?: string
  reason?: string
  arguments?: Record<string, unknown>
}

export interface SSETraceSummaryData {
  total_tokens?: number
  total_cost?: number
  total_duration_ms?: number
  span_count?: number
  spans?: Array<{
    name: string
    status: 'success' | 'failure' | 'running'
    duration_ms?: number
  }>
}

export interface SSEErrorData {
  message: string
}

export interface SSEPreviewData {
  available: boolean
  passed: boolean
  errors: string[]
  logs: string[]
  url?: string
}
