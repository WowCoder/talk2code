#!/usr/bin/env node
/**
 * 对话角色归属的自动化校验（frontend-vue 目前没有测试框架，这里用 tsc 编译
 * 真实源码后直接断言，避免「为了跑测试而复制一份逻辑」——复制品测不出真源码的行为）。
 *
 * 用法：npm run check:role   （在 frontend-vue 目录下）
 *
 * 校验的是产品约束，不是实现细节：
 *   1. 用户消息只能来自用户本人；
 *   2. Agent 侧消息只归属三个角色，无法判断归属时归技术负责人（TL）；
 *   3. 纯内部系统提示不进对话流。
 */
import assert from 'node:assert/strict'
import { loadTsModule } from './_tsm.mjs'

const cases = []
function check(name, fn) {
  try {
    fn()
    cases.push({ name, ok: true })
  } catch (e) {
    cases.push({ name, ok: false, error: e.message })
  }
}

const { mod, cleanup } = await loadTsModule('src/utils/dialogueRole.ts')
const {
  normalizeDialogueMessage,
  normalizeDialogueList,
  roleOfName,
  isUserOriginated,
  TL_NAME,
  DEV_NAME,
  QA_NAME,
} = mod

// ---------- 1. 真用户消息必须留在 user 角色上 ----------
check('真用户消息保持 user', () => {
  const m = normalizeDialogueMessage({ role: 'user', name: '用户', content: '做一个贪吃龙' })
  assert.equal(m.role, 'user')
  assert.equal(m.name, '用户')
})

check('带用户动作卡片的 user 消息保持 user', () => {
  const plan = normalizeDialogueMessage({
    role: 'user', name: '用户', content: '已确认开发计划，开始编码',
    plan_confirmed: { title: 'x' },
  })
  assert.equal(plan.role, 'user')
  const qf = normalizeDialogueMessage({
    role: 'user', name: '用户', content: '答案摘要', question_form: { submitted: true },
  })
  assert.equal(qf.role, 'user')
})

// ---------- 2. 历史数据里错标成 user 的消息必须归位 ----------
check('handoff 起点（历史 user/Handoff）归 TL，不再是用户消息', () => {
  const m = normalizeDialogueMessage({
    role: 'user', name: 'Handoff',
    content: '上一轮已交付。任务状态 handoff 见 .task/DELIVERY.md，请据此继续。',
  })
  assert.equal(m.role, 'agent')
  assert.equal(m.name, TL_NAME)
})

check('QA 契约反馈（历史 user/Catherine）归质量工程师', () => {
  const m = normalizeDialogueMessage({
    role: 'user', name: 'Catherine（质量工程师）',
    content: '文件 js/utils.js 审查未通过（第 1 次），请修复以下问题：…',
  })
  assert.equal(m.role, 'agent')
  assert.equal(m.name, QA_NAME)
})

check('harness 补全提示（历史 user/System）归为隐藏系统提示，不进对话流', () => {
  const m = normalizeDialogueMessage({
    role: 'user', name: 'System',
    content: '你还没有创建所有必需的文件，缺少：script.js, style.css。',
  })
  assert.equal(m.role, 'system')
  assert.equal(m.hidden, true)
})

// ---------- 3. Agent 侧名字归一：三个角色 + 未知归 TL ----------
const agentCases = [
  ['Leon（技术负责人）', TL_NAME],
  ['Henry（开发工程师）', DEV_NAME],
  ['Catherine（质量工程师）', QA_NAME],
  ['QA', QA_NAME],
  ['Evaluator', QA_NAME],
  ['Reviewer', QA_NAME],
  ['Coder', DEV_NAME],
  ['FrontendEngineer', DEV_NAME],
  ['Henry', DEV_NAME],
  ['Henry（开发）', DEV_NAME],
  ['TeamLeader', TL_NAME],
  ['Planner', TL_NAME],
  ['ProductManager', TL_NAME],
  ['Architect', TL_NAME],
  ['AI', TL_NAME],
  ['Bob（架构师）', TL_NAME],
  ['Leon（负责人）', TL_NAME],
  // 「Catherine（产品经理）」与质量工程师 Catherine 同名不同角色：必须显式归 TL，
  // 否则按钮前缀匹配会被判成质量工程师
  ['Catherine（产品经理）', TL_NAME],
  // 未知名字 → 归 TL（会话约束）
  ['不知道是谁', TL_NAME],
  ['', TL_NAME],
  [undefined, TL_NAME],
]
for (const [input, expected] of agentCases) {
  check(`角色归一：${String(input) || '(空)'} → ${expected}`, () => {
    assert.equal(roleOfName(input), expected)
  })
}

check('agent 消息按别名归一（QA → 质量工程师）', () => {
  const m = normalizeDialogueMessage({
    role: 'agent', name: 'QA', content: '## ⚠️ 交付拦截：存在未解决的关键缺陷',
  })
  assert.equal(m.name, QA_NAME)
})

check('iteration_batch 轮次卡片归开发工程师', () => {
  const m = normalizeDialogueMessage({ role: 'iteration_batch', name: 'Henry（开发）', tools: [] })
  assert.equal(m.name, DEV_NAME)
})

// ---------- 4. 已有 system 提示不被误伤（取消通知等要照常显示） ----------
check('role=system 的取消通知保持原样且不被隐藏', () => {
  const m = normalizeDialogueMessage({ role: 'system', name: 'System', content: '操作已被用户取消' })
  assert.equal(m.role, 'system')
  assert.notEqual(m.hidden, true)
  assert.equal(m.content, '操作已被用户取消')
})

check('qa_step / qa_result 卡片不被改动', () => {
  const step = { role: 'qa_step', name: '', qa_step: { ac_id: 'AC-1', action: 'click' } }
  assert.equal(normalizeDialogueMessage(step).role, 'qa_step')
  assert.equal(normalizeDialogueMessage({ role: 'qa_result', qa_result: { ac_id: 'AC-1' } }).role, 'qa_result')
})

// ---------- 5. 幂等：同一份历史重复归一不产生差异 ----------
check('归一化幂等（SSE 重连回放不会改变结果）', () => {
  const list = [
    { role: 'user', name: '用户', content: 'a' },
    { role: 'user', name: 'Handoff', content: 'b' },
    { role: 'agent', name: 'QA', content: 'c' },
    { role: 'system', name: 'System', content: 'd' },
  ]
  const once = normalizeDialogueList(list)
  const twice = normalizeDialogueList(once)
  assert.deepEqual(
    twice.map((m) => [m.role, m.name, m.hidden]),
    once.map((m) => [m.role, m.name, m.hidden])
  )
})

check('isUserOriginated 只认用户身份与用户动作卡片', () => {
  assert.equal(isUserOriginated({ name: '用户' }), true)
  assert.equal(isUserOriginated({ name: '' }), true)
  assert.equal(isUserOriginated({ name: 'Handoff' }), false)
  assert.equal(isUserOriginated({ name: 'System' }), false)
  assert.equal(isUserOriginated({ name: 'Catherine（质量工程师）' }), false)
  assert.equal(isUserOriginated({ name: 'x', question_form: { submitted: true } }), true)
})

cleanup()

const failed = cases.filter((c) => !c.ok)
for (const c of cases) {
  console.log(`${c.ok ? '✅' : '❌'} ${c.name}${c.ok ? '' : `\n     ${c.error}`}`)
}
console.log(`\n${cases.length - failed.length}/${cases.length} 通过`)
process.exit(failed.length ? 1 : 0)
