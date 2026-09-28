#!/usr/bin/env node
/**
 * 用真实的 dialogue_history 回放角色归一化（验收自查工具）。
 *
 * 背景：历史库里混着旧角色名（QA / Evaluator / TeamLeader / AI…）和被错标成
 * user 的系统消息（Handoff / System / Catherine 契约反馈）。归一化只改「显示
 * 归属」，不会重写历史数据 —— 所以必须能拿真实数据验证「刷新后看到的是什么」。
 *
 * 用法：
 *   node scripts/role-replay.mjs dump.json
 *
 * dump.json 形状：{"requirements":[{"id":202,"status":"finished","dialogue_history":[...]}]}
 * （用 backend 侧脚本从 DB 导出；本脚本只读文件，不连库。）
 */
import { readFileSync } from 'node:fs'
import { loadTsModule } from './_tsm.mjs'

const file = process.argv[2]
if (!file) {
  console.error('用法：node scripts/role-replay.mjs <dump.json>')
  process.exit(2)
}
const dump = JSON.parse(readFileSync(file, 'utf-8'))
const reqs = Array.isArray(dump) ? dump : dump.requirements || []

const { mod, cleanup } = await loadTsModule('src/utils/dialogueRole.ts')
const { normalizeDialogueList, TL_NAME, DEV_NAME, QA_NAME } = mod

const ROLE_SET = new Set([TL_NAME, DEV_NAME, QA_NAME])
const USER_NAMES = new Set(['', '用户', 'User', 'user'])
const AGENT_ROLES = new Set(['agent', 'assistant', 'iteration_batch', 'thinking'])

let violations = []
let changedTotal = 0

function histogram(list) {
  const h = new Map()
  for (const m of list) {
    const k = `${m.role} / ${m.name ?? ''}${m.hidden ? ' (hidden)' : ''}`
    h.set(k, (h.get(k) || 0) + 1)
  }
  return [...h.entries()].sort((a, b) => b[1] - a[1])
}

for (const r of reqs) {
  const raw = r.dialogue_history || []
  if (!raw.length) continue
  const norm = normalizeDialogueList(raw)

  const changes = []
  raw.forEach((m, i) => {
    const n = norm[i]
    if (m.role !== n.role || m.name !== n.name || !!m.hidden !== !!n.hidden) {
      changes.push({ i, before: `${m.role} / ${m.name ?? ''}`, after: `${n.role} / ${n.name ?? ''}${n.hidden ? ' (hidden)' : ''}` })
    }
    // 不变量 1：归一化后的 user 消息必须是真用户消息
    if (n.role === 'user' && !USER_NAMES.has(String(n.name ?? '').trim())) {
      violations.push(`req${r.id} #${i} 仍是伪用户消息：${n.name}`)
    }
    // 不变量 2：Agent 侧消息只归属三个角色
    if (AGENT_ROLES.has(n.role) && !ROLE_SET.has(String(n.name ?? '').trim())) {
      violations.push(`req${r.id} #${i} 角色名不在三角色内：${n.role}/${n.name}`)
    }
    // 不变量 3：可见消息里不得混入纯内部提示
    if (AGENT_ROLES.has(n.role) === false && n.role === 'system' && n.hidden !== true
        && /没有创建所有必需的文件|自动语法检查/.test(String(n.content ?? ''))) {
      violations.push(`req${r.id} #${i} 内部系统提示仍可见`)
    }
  })

  changedTotal += changes.length
  console.log(`\n===== req ${r.id}（status=${r.status}，${raw.length} 条）=====`)
  console.log('归一化前角色分布：')
  for (const [k, v] of histogram(raw)) console.log(`  ${String(v).padStart(4)}  ${k}`)
  console.log('归一化后角色分布：')
  for (const [k, v] of histogram(norm)) console.log(`  ${String(v).padStart(4)}  ${k}`)
  console.log(`本次被纠正的消息：${changes.length} 条`)
  for (const c of changes) console.log(`  #${c.i}  ${c.before}  →  ${c.after}`)
}

cleanup()

console.log(`\n合计纠正 ${changedTotal} 条消息。`)
if (violations.length) {
  console.log(`\n❌ 不变量被违反 ${violations.length} 处：`)
  for (const v of violations) console.log(`  - ${v}`)
  process.exit(1)
}
console.log('✅ 不变量全部满足：无伪用户消息、Agent 消息均在三角色内、内部提示不进对话流。')
