/**
 * 产物与发布门禁的纯判定逻辑。
 *
 * 抽成纯函数的原因：这块逻辑此前散在组件里，两处各写一遍快照判定，结果踩了同一个
 * 坑 —— 详情接口的 `code_files` 是**进页面那一刻的快照**，SSE 的 code 事件只写进
 * 实时映射，从不回填快照。只看快照时，全新需求整轮生成期间「产物数」恒为 0，
 * 于是 QA 验收都过了，发布按钮还是灰的，用户必须手动刷新页面（req 202 实测）。
 *
 * 纯函数化之后可以用 `npm run check:gate` 直接断言，不必起浏览器。
 */
import type { RequirementStatus } from '@/types/api'

/** 已终止的需求状态：这些状态下不会再产出新文件 */
export const SETTLED_STATUSES: RequirementStatus[] = [
  'finished',
  'finished_with_issues',
  'needs_user_input',
  'failed',
]

/** 生成中（含可续跑的 interrupted）——产物可能还会增加 */
export const IN_PROGRESS_STATUSES: RequirementStatus[] = [
  'pending',
  'planning',
  'processing',
  'interrupted',
]

/**
 * 已产出的文件名 = 详情快照 ∪ SSE 实时增量（去重，保持快照顺序在前）。
 *
 * 两个方向都必须并集：
 * - 生成中：快照为空、实时映射有值 —— 只看快照会误判「没有产物」；
 * - 刷新后：快照有值、实时映射刚被清空 —— 只看映射同样会误判。
 */
export function mergeProducedFiles(
  snapshot: Array<{ filename?: string | null }> | null | undefined,
  liveKeys: Iterable<string> | null | undefined,
): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const f of snapshot || []) {
    const name = f?.filename
    if (name && !seen.has(name)) {
      seen.add(name)
      out.push(name)
    }
  }
  for (const k of liveKeys || []) {
    if (k && !seen.has(k)) {
      seen.add(k)
      out.push(k)
    }
  }
  return out
}

/**
 * 发布门禁：**通过 QA 验收**（后端把 verify_passed 为真的需求标成 finished）
 * 且**确有产物**。两个条件缺一不可：有产物但验收没过，发出去的站点体验不完整；
 * 验收过了但没有产物（早期需求 162），发布按钮点下去只会报错。
 */
export function canPublish(
  status: RequirementStatus | null | undefined,
  files: string[] | null | undefined,
): boolean {
  return status === 'finished' && (files?.length ?? 0) > 0
}
