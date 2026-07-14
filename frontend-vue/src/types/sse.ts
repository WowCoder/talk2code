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
  | 'trace_summary'
  | 'error'
  | 'preview'
  | 'spec'
  | 'task_list'
  | 'task_update'
  | 'checklist_update'
  | 'evaluator_result'
  | 'cancelled'
  | 'iteration_batch'

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
  name?: string
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

// ===== SDD 新增事件 =====

export interface SSESpecData {
  title?: string
  features?: string[]
  acceptance_criteria?: Array<{
    id: string
    label: string
    how_to_verify?: string
  }>
  file_structure?: string[]
  tech_stack?: {
    css?: string
    storage?: string
    framework?: string
  }
  data_model?: string
  complexity?: string
  implementation_notes?: string
}

export interface SSETaskListData {
  tasks: Array<{
    file: string
    description: string
    status: 'pending' | 'in_progress' | 'completed'
  }>
}

export interface SSETaskUpdateData {
  file: string
  status: 'pending' | 'in_progress' | 'completed'
}

export interface SSEChecklistUpdateData {
  ac_id: string
  passed: boolean
  reason?: string
}

// ===== Evaluator 结果 =====

export interface EvaluatorFinding {
  severity: 'critical' | 'major' | 'minor'
  dimension: string
  description: string
  evidence?: string
  suggestion?: string
}

export interface SSEEvaluatorResultData {
  verdict: 'PASS' | 'NEEDS_WORK'
  summary: string
  overall_score: number
  score: {
    functionality?: number
    runtime?: number
    ui_quality?: number
    acceptance?: number
    code_quality?: number
  }
  findings: EvaluatorFinding[]
  browser_result?: {
    available: boolean
    errors: string[]
    warnings: string[]
  }
}

// ===== 迭代批量事件 =====

export interface SSEIterationBatchTool {
  name: string
  readable: string
  success: boolean
  arguments?: Record<string, unknown>
}

export interface SSEIterationBatchData {
  iteration: number
  coder_name: string
  thinking_preview: string
  agent_text: string
  tools: SSEIterationBatchTool[]
}

// Task 状态联合类型增加 blocked/failed
export type TaskStatus = 'pending' | 'in_progress' | 'completed' | 'blocked' | 'failed'
