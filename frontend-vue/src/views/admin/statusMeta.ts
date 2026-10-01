/**
 * 需求状态的展示元数据 —— 首页列表、筛选 chip、详情页头部共用同一份。
 *
 * 为什么必须是一份：这三个地方经常同时开在两个标签页里。各写一份的结果就是
 * 同一个状态在列表里叫「已完成」、在详情里原样吐出 `finished`；或者列表标绿、
 * 详情标灰 —— 差异会立刻被读成「数据不一致」，而要排查的其实只是两份 map。
 *
 * tone 是给头部 chip 用的语义档位（ok / warn / bad / info / muted），
 * color 是给列表圆点用的具体色值。两者都由后端下发的 status 字符串驱动。
 */
export interface StatusMeta {
  label: string
  color: string
  tone: 'ok' | 'warn' | 'bad' | 'info' | 'muted'
}

export const REQ_STATUS: Record<string, StatusMeta> = {
  finished: { label: '已完成', color: 'oklch(55% 0.1 155)', tone: 'ok' },
  finished_with_issues: { label: '有问题', color: 'oklch(62% 0.13 65)', tone: 'warn' },
  needs_user_input: { label: '待用户处理', color: 'oklch(65% 0.12 85)', tone: 'warn' },
  processing: { label: '处理中', color: 'oklch(50% 0.1 250)', tone: 'info' },
  planning: { label: '待确认', color: 'oklch(62% 0.09 230)', tone: 'info' },
  pending: { label: '等待中', color: 'oklch(72% 0.1 85)', tone: 'info' },
  interrupted: { label: '已中断', color: 'oklch(62% 0.01 70)', tone: 'warn' },
  failed: { label: '失败', color: 'oklch(50% 0.18 25)', tone: 'bad' },
  // 孤儿事件（有 agent_events、查不到 Requirements 行）会落到这里，
  // 显示「未知」而不是空白 —— 空白会被读成「前端不认识这个状态」
  unknown: { label: '未知', color: 'oklch(65% 0.01 70)', tone: 'muted' },
}

export const statusMeta = (s: string): StatusMeta =>
  REQ_STATUS[s] ?? REQ_STATUS.unknown

export const statusColor = (s: string): string => statusMeta(s).color
export const statusLabel = (s: string): string => statusMeta(s).label
export const statusTone = (s: string): StatusMeta['tone'] => statusMeta(s).tone
