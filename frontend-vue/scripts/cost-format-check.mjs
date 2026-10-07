#!/usr/bin/env node
/**
 * 成本展示口径的自动化校验。
 *
 * 回归对象：同一个成本数字，后台列表与详情页显示 ¥，需求详情页的 token bar 与
 * 执行明细却显示 $ —— 换个页面换一种币种，读的人会以为中间做过汇率换算。
 * 根因是格式化逻辑散落在 5 个组件里各写一遍，所以收口到 src/utils/cost.ts，
 * 本脚本守住两件事：
 *   1. 口径本身：符号、精度、以及「没记录到成本」不能被显示成 ¥0；
 *   2. 没人绕开它自拼符号：源码里出现「美元符号与 cost 同行」直接判失败。
 *
 * 用法：npm run check:cost
 */
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { loadTsModule, projectRoot } from './_tsm.mjs'

const cases = []
function check(name, fn) {
  try {
    fn()
    cases.push({ name, ok: true })
  } catch (e) {
    cases.push({ name, ok: false, error: e.message })
  }
}

const { mod, cleanup } = await loadTsModule('src/utils/cost.ts')
const { formatCost, COST_SYMBOL } = mod

// ---------- 口径本身 ----------
check('全站统一用 ¥ 符号', () => {
  assert.equal(COST_SYMBOL, '¥')
  assert.equal(formatCost(0.0827), '¥0.0827')
})

check('默认 4 位小数，够单次生成的量级（分/厘）显示', () => {
  assert.equal(formatCost(0.0001), '¥0.0001')
  assert.equal(formatCost(1.5), '¥1.5000')
})

check('聚合值可指定 2 位小数', () => {
  assert.equal(formatCost(12.3456, 2), '¥12.35')
})

check('0 不带无意义的尾随小数', () => {
  assert.equal(formatCost(0), '¥0')
  assert.equal(formatCost(0, 2), '¥0')
})

check('无数据出占位符，不冒充 ¥0', () => {
  // 「这次没记录到成本」和「这次花了 0 元」必须是两件事
  assert.equal(formatCost(null), '—')
  assert.equal(formatCost(undefined), '—')
  assert.equal(formatCost(NaN), '—')
  assert.equal(formatCost(Infinity), '—')
})

// ---------- 不得回退到各写各的 ----------
function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) yield* walk(full)
    else if (/\.(vue|ts)$/.test(entry)) yield full
  }
}

check('源码里不再出现「美元符号 + cost」的自拼写法', () => {
  const offenders = []
  for (const file of walk(join(projectRoot, 'src'))) {
    if (file.endsWith(join('utils', 'cost.ts'))) continue
    readFileSync(file, 'utf8')
      .split('\n')
      .forEach((line, i) => {
        if (/\$/.test(line) && /cost/i.test(line)) {
          offenders.push(`${file.replace(projectRoot + '/', '')}:${i + 1}  ${line.trim()}`)
        }
      })
  }
  assert.deepEqual(offenders, [], `成本展示必须走 formatCost：\n${offenders.join('\n')}`)
})

check('每个展示成本的页面都接同一个实现', () => {
  const must = [
    'components/detail/TokenBar.vue',
    'components/detail/ExecutionPanel.vue',
    'views/admin/AdminTracesView.vue',
    'views/admin/AdminTraceDetailView.vue',
    'views/admin/AdminMetricsView.vue',
  ]
  const missing = must.filter(
    (rel) => !readFileSync(join(projectRoot, 'src', rel), 'utf8').includes("from '@/utils/cost'")
  )
  assert.deepEqual(missing, [], `这些文件没接 @/utils/cost：${missing.join(', ')}`)
})

cleanup()

const failed = cases.filter((c) => !c.ok)
for (const c of cases) {
  console.log(`${c.ok ? '✅' : '❌'} ${c.name}${c.ok ? '' : `\n     ${c.error}`}`)
}
console.log(`\n${cases.length - failed.length}/${cases.length} 通过`)
process.exit(failed.length ? 1 : 0)
