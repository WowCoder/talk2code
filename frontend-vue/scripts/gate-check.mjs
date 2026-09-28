#!/usr/bin/env node
/**
 * 发布门禁 / 产物判定的自动化校验。
 *
 * 回归对象（验收实测，req 202）：质量工程师验收通过后「发布」TAB 一直不可点，
 * 必须手动刷新页面才能发布。两个根因都在门禁判定里：
 *   1. 产物只看详情接口的快照（生成期间恒为空），不看 SSE 实时增量；
 *   2. 需求终态没随 complete 事件同步，status 永远停在 processing。
 *
 * 用法：npm run check:gate
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

const { mod, cleanup } = await loadTsModule('src/utils/publishGate.ts')
const { mergeProducedFiles, canPublish, SETTLED_STATUSES, IN_PROGRESS_STATUSES } = mod

// ---------- 产物并集 ----------
check('生成中：快照为空、实时增量有值 → 必须算有产物', () => {
  const files = mergeProducedFiles([], ['index.html', 'js/game.js'])
  assert.deepEqual(files, ['index.html', 'js/game.js'])
})

check('刷新后：快照有值、实时映射已清空 → 必须算有产物', () => {
  const files = mergeProducedFiles(
    [{ filename: 'index.html' }, { filename: 'style.css' }],
    [],
  )
  assert.deepEqual(files, ['index.html', 'style.css'])
})

check('两边都有 → 去重且保持快照在前', () => {
  const files = mergeProducedFiles(
    [{ filename: 'index.html' }, { filename: 'style.css' }],
    ['style.css', 'js/app.js'],
  )
  assert.deepEqual(files, ['index.html', 'style.css', 'js/app.js'])
})

check('脏数据（缺 filename / 空串）不被计入', () => {
  const files = mergeProducedFiles(
    [{ filename: '' }, {}, { filename: null }],
    ['', null, undefined, 'index.html'],
  )
  assert.deepEqual(files, ['index.html'])
})

check('全空 → 空数组', () => {
  assert.deepEqual(mergeProducedFiles(null, null), [])
  assert.deepEqual(mergeProducedFiles(undefined, []), [])
})

// ---------- 发布门禁 ----------
check('QA 通过 + 有产物 → 可发布', () => {
  assert.equal(canPublish('finished', ['index.html']), true)
})

check('QA 通过但无产物 → 不可发布', () => {
  assert.equal(canPublish('finished', []), false)
  assert.equal(canPublish('finished', null), false)
})

check('有产物但验收未通过 → 不可发布', () => {
  for (const st of ['processing', 'planning', 'pending', 'interrupted',
                    'finished_with_issues', 'needs_user_input', 'failed', null]) {
    assert.equal(canPublish(st, ['index.html']), false, `status=${st} 不应放行`)
  }
})

check('回归场景：全新需求在生成中完成验收（快照空 + 实时有产物 + 终态已同步）', () => {
  // complete 事件把终态同步成 finished 之前 —— 不可发布
  const beforeSync = canPublish('processing', mergeProducedFiles([], ['index.html']))
  assert.equal(beforeSync, false)
  // complete 事件同步终态之后 —— 无需刷新页面即可发布
  const afterSync = canPublish('finished', mergeProducedFiles([], ['index.html']))
  assert.equal(afterSync, true)
})

check('状态分组无重叠：终止态与进行中互斥', () => {
  const overlap = SETTLED_STATUSES.filter((s) => IN_PROGRESS_STATUSES.includes(s))
  assert.deepEqual(overlap, [])
})

cleanup()

const failed = cases.filter((c) => !c.ok)
for (const c of cases) {
  console.log(`${c.ok ? '✅' : '❌'} ${c.name}${c.ok ? '' : `\n     ${c.error}`}`)
}
console.log(`\n${cases.length - failed.length}/${cases.length} 通过`)
process.exit(failed.length ? 1 : 0)
