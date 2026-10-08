/**
 * 后台轨迹视图的共享类型。
 *
 * 为什么抽出来：需求轨迹页与评测页用的是**同一套**渲染组件
 * （TraceTimeline / EventDetailPanel），它们吃的就是这些结构。
 * 类型若在两个文件里各写一份，改一个字段就会有一边悄悄失配 ——
 * 而失配在界面上只表现为「某个地方空着」。
 */

export interface Turn {
  turn_index: number
  mode: string
  event_count: number
  llm_calls: number
  started_at: string | null
  ended_at: string | null
  duration_ms: number
}

/** 阶段时间线的一段 —— 按阶段聚合（不是按连续段），回答「这个阶段共花了多久」 */
export interface StageSeg {
  stage: string
  events: number
  llm_calls: number
  ms: number
  started_at: string | null
  ended_at: string | null
}

/** 轨迹级汇总 —— 轮次/题可以单选切换，但「总共花了多少」必须一眼可见 */
export interface Summary {
  turns: number
  events: number
  llm_calls: number
  tool_calls: number
  error_count: number
  tokens: number
  tokens_in: number
  tokens_out: number
  /** cached 是 tokens_in 的子集，不是并列的第三类 token */
  cached_tokens: number
  /** null = 没有任何调用上报缓存信息（≠ 0%，0% 是查过但没命中） */
  cache_hit_rate: number | null
  cache_reported_calls: number
  cost: number
  duration_ms: number
  // 首调用 + 长尾 == duration_ms，由后端保证；前端只负责展示，不自己凑数
  first_llm_ms: number
  tail_ms: number
  started_at: string | null
  last_event_at: string | null
  stages: StageSeg[]
  by_model: { model: string; calls: number; cost: number }[]
  verify_verdicts: (string | null)[]
  repair_rounds: number
  passed: boolean
}

/** 轨迹所属对象的基本信息（随 /turns 一起下发，省掉「去列表里捞标题」那次请求） */
export interface TraceOwner {
  id: number
  title: string
  status: string
  creator: string
  created_at: string | null
  trace_id: string | null
}

export interface Ev {
  id: number
  seq: number
  turn_index: number
  iteration: number | null
  ts: string | null
  kind: string
  stage: string | null
  label: string | null
  status: string
  model: string | null
  duration_ms: number | null
  tokens_in: number
  tokens_out: number
  has_payload: boolean
  message_count: number
}

/**
 * missing：正文没存下来（blob 缺失）。后端不再把它静默成空串 ——
 * 空串分不清「这条本来就是空的」和「没存下来」，后者是事故。
 */
export interface Msg {
  index: number
  role: string
  content: string | null
  char_len: number
  missing?: boolean
  name?: string
}

/** 各家 LLM 的 tool_call 形状不统一：OpenAI 系是 function.{name,arguments} 嵌套，
 *  部分厂商是扁平 name/arguments，两种都要能读。 */
export interface ToolCall {
  function?: { name?: string; arguments?: string }
  name?: string
  arguments?: string
}

export interface Detail {
  id: number
  label: string | null
  kind: string
  ts: string | null
  model: string | null
  duration_ms: number | null
  trace_id: string | null
  call_id: string | null
  tokens_in: number
  tokens_out: number
  messages: Msg[]
  tools: unknown[] | null
  response: { content?: string; tool_calls?: ToolCall[] } | null
  tool_content: string | null
  /** tool_call 事件的工具参数（LLM 事件里则是 messages/tools 之外的请求参数） */
  request_params: Record<string, unknown> | null
  /** true/false：大参数按 hash 还原是否成功；false 时 arguments 是残缺的 */
  args_resolved?: boolean
  /** 里程碑事件的结论字段（判定、得分、未达成 AC、修复轮次…） */
  meta: Record<string, unknown> | null
}

export interface KindSpec {
  label: string | null
  color: string
  stage: string | null
}

/** 事件契约（后端 /api/admin/traces/contract 下发）—— 阶段与事件类型的中文名/配色 */
export interface Contract {
  kinds: Record<string, KindSpec>
  stages: Record<string, { label: string | null; color: string }>
  filter_kinds: string[]
  fallback_kind: KindSpec
  fallback_stage: { label: string | null; color: string }
}

export interface DetailTab {
  key: string
  label: string
  count?: number
}

/** 事件类型的中文名兜底（契约还没拉到时的首帧） */
export const KIND_FALLBACK: Record<string, string> = {
  intent: '意图', memory: '记忆', clarify: '澄清', plan: '规划', coding: '编码',
  llm_turn: 'LLM', tool_call: '工具', verify: '验收', repair: '修复', deliver: '交付',
}
