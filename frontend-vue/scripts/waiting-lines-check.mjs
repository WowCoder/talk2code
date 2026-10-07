#!/usr/bin/env node
/**
 * 「等待俏皮话」的自动化校验。
 *
 * 为什么要有这个脚本：文案放在配置里随时可改，改坏了在界面上的表现只是
 * 「那一行空了 / 一直重复同一句 / 撑破卡片」，肉眼盯界面基本抓不住——
 * 尤其「某个阶段忘了配文案」这种，只有当流程真的走到那个阶段才暴露。
 * 这里用项目自带的 tsc 编译**真实源码**后断言（见 _tsm.mjs），
 * 断言跑在真配置上，而不是复制一份文案来测。
 *
 * 校验的是产品约束，不是实现细节：
 *   1. 每个阶段都有够轮播两分钟的文案，配置被改空也能安全兜底（界面永不空白）；
 *   2. 轮播一轮之内不重复、一轮覆盖全部、换阶段立刻换池；
 *   3. 文案卫生：不长不重、不带负面词（等的时候看到「失败/卡死」只会更焦虑）。
 *
 * 用法：npm run check:waiting   （在 frontend-vue 目录下）
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

const { mod, cleanup } = await loadTsModule('src/utils/waitingLines.ts')
const {
  WAITING_LINES,
  WAITING_STAGES,
  FALLBACK_WAITING_LINE,
  resolvePool,
  createLineRotator,
} = mod

// 顺序随机但可注入，测试里用固定序列保证断言稳定
function seqRng(values) {
  let i = 0
  return () => values[i++ % values.length] ?? 0
}

/**
 * 每池最少条数：10 秒一条 → 一轮要撑住 2 分钟不重复。
 * 这个数字是有依据的，不是拍脑袋：条数不够时用户会在一两分钟内反复看到
 * 同一句，「它还在动」的信号就失真了。
 */
const MIN_LINES_PER_STAGE = 12
const MIN_LINES_UNKNOWN = 8

// ---------- 1. 配置完整性 ----------
check('四个执行阶段都配了非空文案', () => {
  assert.deepEqual([...WAITING_STAGES], ['planning', 'coding', 'verifying', 'repairing'])
  for (const stage of WAITING_STAGES) {
    const bucket = WAITING_LINES.stages[stage]
    assert.ok(bucket, `阶段 ${stage} 没有配置项`)
    assert.ok(Array.isArray(bucket.lines) && bucket.lines.length > 0, `阶段 ${stage} 的文案是空的`)
  }
})

check(`每个阶段至少 ${MIN_LINES_PER_STAGE} 条（保证 2 分钟不重复）`, () => {
  for (const stage of WAITING_STAGES) {
    const n = WAITING_LINES.stages[stage].lines.length
    assert.ok(n >= MIN_LINES_PER_STAGE, `阶段 ${stage} 只有 ${n} 条，轮播很快就重复`)
  }
})

check(`未知阶段至少 ${MIN_LINES_UNKNOWN} 条且有兜底句`, () => {
  assert.ok(WAITING_LINES.unknown.lines.length >= MIN_LINES_UNKNOWN)
  assert.ok(FALLBACK_WAITING_LINE.length > 0)
})

check('轮换间隔在合理区间（5~20 秒）', () => {
  assert.ok(
    WAITING_LINES.rotateMs >= 5000 && WAITING_LINES.rotateMs <= 20000,
    `rotateMs=${WAITING_LINES.rotateMs}，太快读不完、太慢像卡住`
  )
})

// ---------- 2. 取池：阶段 / 兜底 ----------
check('已知阶段取自己的文案池', () => {
  assert.deepEqual(resolvePool('coding'), WAITING_LINES.stages.coding.lines)
})

check('未知阶段退回兜底池', () => {
  for (const stage of ['', null, undefined, 'who-knows']) {
    assert.deepEqual(resolvePool(stage), WAITING_LINES.unknown.lines)
  }
})

check('配置被改空也不会返回空池（界面不会空一行）', () => {
  const cfg = { rotateMs: 10000, stages: {}, unknown: { lines: [] } }
  assert.deepEqual(resolvePool('coding', cfg), [FALLBACK_WAITING_LINE])
})

// ---------- 3. 轮播：一轮不重复、覆盖全部、换阶段换池 ----------
check('一轮之内不重复，且一轮覆盖全部文案', () => {
  const rot = createLineRotator(WAITING_LINES, seqRng([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]))
  rot.setStage('coding')
  const pool = WAITING_LINES.stages.coding.lines
  const seen = []
  for (let i = 0; i < pool.length; i++) seen.push(rot.next())
  assert.equal(new Set(seen).size, pool.length, `一轮内出现重复：${seen.join(' / ')}`)
  assert.deepEqual([...seen].sort(), [...pool].sort())
})

check('下一轮重新洗牌，不会永久停在某一条', () => {
  const rot = createLineRotator(WAITING_LINES, seqRng([0.9, 0.1, 0.2, 0.3, 0.4, 0.5]))
  rot.setStage('coding')
  const pool = WAITING_LINES.stages.coding.lines
  for (let i = 0; i < pool.length; i++) rot.next()
  assert.ok(pool.includes(rot.next()), '新一轮的文案不在本阶段池里')
})

check('换阶段立刻换池，不再沿用上一阶段的文案', () => {
  const rot = createLineRotator(WAITING_LINES, seqRng([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]))
  rot.setStage('coding')
  assert.ok(WAITING_LINES.stages.coding.lines.includes(rot.next()))
  rot.setStage('verifying')
  assert.ok(WAITING_LINES.stages.verifying.lines.includes(rot.next()))
})

check('同一阶段连续两次取文案不会撞同一句', () => {
  const rot = createLineRotator(WAITING_LINES)
  rot.setStage('coding')
  let same = 0
  let prev = rot.next()
  for (let i = 0; i < 80; i++) {
    const cur = rot.next()
    if (cur === prev) same++
    prev = cur
  }
  // 一轮结束后换袋，允许出现接缝重复；但重复次数不应超过「轮数」
  const rounds = 80 / WAITING_LINES.stages.coding.lines.length + 1
  assert.ok(same <= Math.ceil(rounds), `同一句连续出现过于频繁：${same} 次`)
})

check('池里只有一条时不会崩溃，且始终返回它', () => {
  const cfg = { rotateMs: 10000, stages: { coding: { lines: ['就这一句'] } }, unknown: { lines: ['兜底'] } }
  const rot = createLineRotator(cfg, seqRng([0.5]))
  rot.setStage('coding')
  for (let i = 0; i < 5; i++) assert.equal(rot.next(), '就这一句')
})

check('空池不抛错，返回安全兜底', () => {
  const cfg = { rotateMs: 10000, stages: {}, unknown: { lines: [] } }
  const rot = createLineRotator(cfg, seqRng([0.5]))
  rot.setStage('coding')
  assert.equal(rot.next(), FALLBACK_WAITING_LINE)
})

check('没调 setStage 直接取文案也不会崩', () => {
  const rot = createLineRotator(WAITING_LINES, seqRng([0.3]))
  assert.ok(WAITING_LINES.unknown.lines.includes(rot.next()))
})

check('reset 之后重新从本阶段池取', () => {
  const rot = createLineRotator(WAITING_LINES)
  rot.setStage('coding')
  rot.next()
  rot.reset()
  assert.ok(WAITING_LINES.stages.coding.lines.includes(rot.next()))
})

// ---------- 4. 文案卫生：长度 / 去重 / 禁负面词 ----------
const BANNED = ['失败', '崩溃', '卡死', '卡住', '报错', '超时', '死机', 'bug', 'Bug', '完蛋']

function allPools() {
  const list = []
  for (const stage of WAITING_STAGES) list.push([`${stage}.lines`, WAITING_LINES.stages[stage].lines])
  list.push(['unknown.lines', WAITING_LINES.unknown.lines])
  return list
}

check('同一池内没有重复文案', () => {
  for (const [name, pool] of allPools()) {
    assert.equal(new Set(pool).size, pool.length, `${name} 里有重复文案`)
  }
})

check('跨池也没有重复文案（不同阶段之间不该撞句）', () => {
  const seen = new Map()
  for (const [name, pool] of allPools()) {
    for (const line of pool) {
      if (seen.has(line)) assert.fail(`「${line}」同时出现在 ${seen.get(line)} 和 ${name}`)
      seen.set(line, name)
    }
  }
})

check('每条文案都短到能一行放得下（≤ 24 字）', () => {
  for (const [name, pool] of allPools()) {
    for (const line of pool) {
      assert.ok(line.length <= 24, `${name} 里「${line}」${line.length} 字，会撑破卡片`)
      assert.ok(line.trim().length > 0, `${name} 里有空文案`)
    }
  }
})

check('文案不带负面词（等待中看到只会更焦虑）', () => {
  for (const [name, pool] of allPools()) {
    for (const line of pool) {
      for (const bad of BANNED) {
        assert.ok(!line.includes(bad), `${name} 里「${line}」含有负面词「${bad}」`)
      }
    }
  }
})

check('文案不带句号（一行短句，加句号显得生硬）', () => {
  for (const [name, pool] of allPools()) {
    for (const line of pool) {
      assert.ok(!/[。！？.!?]$/.test(line), `${name} 里「${line}」结尾多了标点`)
    }
  }
})

cleanup()

const failed = cases.filter((c) => !c.ok)
for (const c of cases) {
  console.log(`${c.ok ? '✅' : '❌'} ${c.name}${c.ok ? '' : `\n     ${c.error}`}`)
}
console.log(`\n${cases.length - failed.length}/${cases.length} 通过`)
process.exit(failed.length ? 1 : 0)
