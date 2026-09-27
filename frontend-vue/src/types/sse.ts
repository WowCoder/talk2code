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
  | 'iteration_start'
  | 'iteration_append'
  | 'iteration_end'
  | 'qa_step'
  | 'qa_start'
  | 'qa_result'

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
  /**
   * "当前在做什么"的动作描述（如"正在创建 js/app.js"）。
   * 后端已改为推动作而非角色名——角色名不携带进展信息。
   */
  current_agent: string
  progress: number
  status: string
  /** 阶段标识，用于渲染阶段指示器；缺省表示沿用上一阶段 */
  stage?: 'planning' | 'coding' | 'verifying' | 'repairing'
}

/**
 * 服务端周期性心跳（每 15s 一次，仅在无业务事件时发送）。
 *
 * LLM 请求可能挂起 60~150 秒且期间没有任何业务事件，此前服务端只发 SSE 注释行，
 * 前端收不到任何东西，观感等同「卡死」。改为真实事件后，前端可据此显示
 * 「仍在处理 · 已等待 Ns」，把静默期变成可见的等待。
 */
export interface SSEHeartbeatData {
  requirement_id: number
  elapsed_s: number
  timestamp?: string
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
    passed?: boolean | null
    reason?: string
    /** 四态信号: passed / compromised / unverified / not_applicable / fail / pending */
    state?: string
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

export interface SSETask {
  file: string
  description: string
  status: TaskStatus
}

export interface SSETaskListData {
  tasks: SSETask[]
}

export interface SSETaskUpdateData {
  file: string
  status: 'pending' | 'in_progress' | 'completed'
}

export interface SSEChecklistUpdateData {
  ac_id: string
  passed: boolean
  reason?: string
  /** 四态信号: passed / compromised / unverified / not_applicable / fail / pending */
  state?: string
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
  ac_results?: ACResult[]
  browser_result?: {
    available: boolean
    errors: string[]
    warnings: string[]
  }
  fast_pass?: boolean
}

// ===== AC 逐条验收结果 =====

export interface ACResult {
  ac_id: string
  label?: string
  passed: boolean
  failures?: string[]
  steps_executed?: number
}

// ===== 迭代批量事件 =====

export interface SSEIterationBatchTool {
  name: string
  readable: string
  success: boolean
  blocked?: boolean
  arguments?: Record<string, unknown>
}

export interface SSEIterationBatchData {
  iteration: number
  coder_name: string
  thinking_preview: string
  agent_text: string
  tools: SSEIterationBatchTool[]
}

// ===== 迭代轮次实时累积（取代整轮一次性 iteration_batch）=====
// 一轮开始（iteration_start）→ 过程每步 iteration_append 实时填充 →
// 轮次结束（iteration_end）固定卡片。刷新页面时由 dialogue_history 的迭代记录恢复静态卡片。

/** iteration_start：一张可实时累积的轮次卡片 */
export interface SSEIterationStartData {
  iteration: number
  coder_name: string
  thinking_preview?: string
  agent_text?: string
  tools?: SSEIterationBatchTool[]
  content?: string
}

/** iteration_append：单个工具操作，实时追加进当前轮次卡片 */
export interface SSEIterationAppendData {
  tool: SSEIterationBatchTool
}

/** iteration_end：固定当前轮次卡片（不再实时变化） */
export interface SSEIterationEndData {
  iteration: number
  content?: string
}

// ===== QA 验收逐步操作流 =====

export type QAStepStatus = 'ok' | 'fail' | 'error' | 'na'

export interface SSEQAStepData {
  ac_id: string
  action: string
  selector?: string
  value?: string
  status: QAStepStatus
  detail?: string
  timestamp?: string
}

// ===== QA 验收项（AC）聚合 =====
// 一个 AC 一张卡片：qa_start 建卡 → qa_step 逐步实时填充 → qa_result 固定并给出结论。
// 逐步事件只做实时展示不落库，落库的是 qa_result（含内嵌 steps），
// 因此刷新后恢复的仍是同一张 AC 卡片，不会退化成几百条独立行（需求 196 实测 340 条）。

export interface SSEQAAcData {
  ac_id: string
  label?: string
  status?: 'running' | QAStepStatus
  passed?: boolean
  steps?: SSEQAStepData[]
  start_ts?: string | null
  end_ts?: string | null
  summary?: string
}

// Task 状态联合类型增加 blocked/failed
export type TaskStatus = 'pending' | 'in_progress' | 'completed' | 'blocked' | 'failed'
