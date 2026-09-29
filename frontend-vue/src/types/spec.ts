// ===== Plan 的统一数据契约 =====
//
// 此前这份结构在 4 个地方各写一遍（types/sse.ts、types/api.ts、
// PlanSummaryCard.vue、SpecPanel.vue），结果就是 SSE 明明推了 acceptance_criteria，
// 卡片组件的本地 interface 却没声明它 —— 数据到了浏览器门口，一行都没渲染。
// 用户签字的是 A 份内容，系统判定通过与否看的是 B 份内容。
//
// 现在所有地方共用这一份定义：加了字段就一起有，删了字段就一起消失。

export interface AcceptanceCriterion {
  id: string
  label: string
  /** 对应 features 中的哪一项，用于校验「每个功能都有验收覆盖」 */
  feature?: string
  /** 页面上的语义区域（人话描述），验收脚本据此定位元素 */
  anchor?: string
  how_to_verify?: string
  passed?: boolean | null
  reason?: string
  /** 四态信号: passed / compromised / unverified / not_applicable / fail / pending */
  state?: string
}

export interface PlanTechStack {
  css?: string
  storage?: string
  framework?: string
}

/**
 * 一份 plan 承担两种职责，字段按职责分区，不要混排：
 *
 * - 需求契约（requirement_restated / features / assumptions / acceptance_criteria）：
 *   给用户读、给用户签字。同时也是 verify 判定通过与否的唯一依据——
 *   两者必须是同一份内容，否则用户签的字没有任何约束力。
 * - 工程契约（tech_stack / file_structure / complexity）：
 *   给机器读。展示时默认折叠，用户不必看懂。
 */
export interface PlanSpec {
  title?: string

  // ---- 需求契约 ----
  /** 一句话复述用户要什么，人话，无技术名词 */
  requirement_restated?: string
  features?: string[]
  /** 用户没提、但我们替他定了的默认决定 */
  assumptions?: string[]
  acceptance_criteria?: AcceptanceCriterion[]

  // ---- 工程契约 ----
  tech_stack?: PlanTechStack
  file_structure?: string[]
  complexity?: string

  /** TL 失败时的错误提示（成功时为 undefined） */
  error?: string
}
