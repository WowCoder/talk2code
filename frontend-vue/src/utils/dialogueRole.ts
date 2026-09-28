/**
 * 对话流的角色归属归一化 —— 谁说的，就说成谁的。
 *
 * 规则（产品约束，不是实现细节）：
 * 1. 用户消息只能来自用户本人。后端 / 历史数据里凡是系统注入、Agent 之间
 *    转发的内部消息，都不允许挂在 user 角色上 —— 否则用户会在自己的消息框里
 *    看到自己从没发过的话（实测：交付 handoff 起点、QA 契约反馈、harness 补
 *    全提示都曾以 role=user 落库；req 79/144/191/202 等等）。
 * 2. Agent 侧消息只归属三个角色：技术负责人 / 开发工程师 / 质量工程师。
 *    无法判断归属时，一律归技术负责人（TL）。
 * 3. 真正的内部系统提示（补全提醒、自动语法检查等）不进对话流：它们是给
 *    Agent 看的，不是对话回合。
 *
 * 归一化放在「消息进入 store」的唯一入口，而不是渲染层：渲染层改的是显示，
 * 幂等键（role+name+content+timestamp）仍会按原始 name 去重，实时推送与
 * 历史恢复两条路径会对不上号，出现重复卡片。
 */
import type { DialogueMessage } from '@/types/api'

/** 三个角色的规范名（与后端 harness/agent_names.py 一字不差） */
export const TL_NAME = 'Leon（技术负责人）'
export const DEV_NAME = 'Henry（开发工程师）'
export const QA_NAME = 'Catherine（质量工程师）'

/** 角色简名 → 规范名。新增角色必须先改这里，再改后端 agent_names.py。 */
export const ROLE_ALIASES: Record<string, string> = {
  // ---- 技术负责人（需求分析 / 计划 / 架构 / 产品）----
  [TL_NAME]: TL_NAME,
  'Leon': TL_NAME,
  'Leon（负责人）': TL_NAME,
  'TeamLeader': TL_NAME,
  'Planner': TL_NAME,
  'ProductManager': TL_NAME,
  'Architect': TL_NAME,
  'AI': TL_NAME,
  'Bob（架构师）': TL_NAME,
  // 历史上有过一位「Catherine（产品经理）」——产品经理已并入 TL。
  // 必须显式写死：否则按名字前缀匹配会把她误判成质量工程师 Catherine。
  'Catherine（产品经理）': TL_NAME,
  // ---- 开发工程师 ----
  [DEV_NAME]: DEV_NAME,
  'Henry': DEV_NAME,
  'Henry（开发）': DEV_NAME,
  'Coder': DEV_NAME,
  'FrontendEngineer': DEV_NAME,
  // ---- 质量工程师 ----
  [QA_NAME]: QA_NAME,
  'Catherine': QA_NAME,
  'QA': QA_NAME,
  'Evaluator': QA_NAME,
  'Reviewer': QA_NAME,
  // ---- 系统身份（不参与角色着色）----
  'System': 'System',
  'AutoLint': 'AutoLint',
}

/** 纯内部提示的名字：给 Agent 看的，不进对话流 */
const INTERNAL_ONLY_NAMES = new Set(['System', 'AutoLint'])

/** 用户本人发言的判定：真用户消息之外，带这些卡片字段的也算（它们是用户在界面上的动作） */
const USER_ACTION_FIELDS = [
  'question_form',
  'plan_confirmed',
  'plan_feedback',
] as const

/**
 * 把任意名字归到三个角色之一；无法判断时归技术负责人。
 */
export function roleOfName(name: string | undefined | null): string {
  const n = String(name ?? '').trim()
  if (!n) return TL_NAME
  const hit = ROLE_ALIASES[n]
  if (hit) return hit
  return TL_NAME
}

/**
 * 这条消息是不是「用户本人发的」。
 * 判据只认证据：名字是后端写入的用户身份，或者带着用户动作卡片字段。
 */
export function isUserOriginated(msg: Partial<DialogueMessage> & Record<string, any>): boolean {
  if (USER_ACTION_FIELDS.some((f) => (msg as any)[f])) return true
  const n = String(msg?.name ?? '').trim()
  return n === '' || n === '用户' || n === 'User' || n === 'user'
}

/**
 * 归一化单条对话消息（幂等：已规范的输入原样返回）。
 *
 * 返回新对象，不改入参 —— 入参会同时被幂等键计算与持久化回写使用。
 */
export function normalizeDialogueMessage<T extends Partial<DialogueMessage> & Record<string, any>>(
  msg: T
): T {
  if (!msg || typeof msg !== 'object') return msg
  const role = String((msg as any).role ?? '')
  const name = String((msg as any).name ?? '').trim()

  // 1) user：只有真用户消息才配留在 user 角色上
  if (role === 'user') {
    if (isUserOriginated(msg)) return msg
    if (INTERNAL_ONLY_NAMES.has(name)) {
      // 系统注入的内部提示（补全提醒 / 自动检查）。它既不是用户说的，也不是
      // 某个角色的对话回合，落成气泡只会让对话流变脏 → 标 hidden 只进历史不进视图。
      return { ...msg, role: 'system', hidden: true }
    }
    // 其余（QA 契约反馈、交付 handoff 起点等）是 Agent 侧消息被错标成 user
    // → 按规则回到正确的角色身上，未知则归 TL。
    return { ...msg, role: 'agent', name: roleOfName(name) }
  }

  // 2) agent / assistant / iteration_batch / thinking：角色名归一
  if (
    role === 'agent' ||
    role === 'assistant' ||
    role === 'iteration_batch' ||
    role === 'thinking'
  ) {
    if (name && ROLE_ALIASES[name] === name) return msg
    return { ...msg, name: roleOfName(name) }
  }

  return msg
}

/** 批量归一化（历史恢复路径用）。 */
export function normalizeDialogueList<T extends Partial<DialogueMessage> & Record<string, any>>(
  list: T[] | undefined | null
): T[] {
  if (!Array.isArray(list)) return []
  return list.map((m) => normalizeDialogueMessage(m))
}
