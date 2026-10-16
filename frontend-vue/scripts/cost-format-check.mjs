#!/usr/bin/env node
/**
 * 成本展示口径的自动化校验。
 *
 * 编译真实 TS 实现验证 USD、微小金额、零与未知，以及 CSV 数值精度。
 * 同时守住展示入口，避免不同页面各自拼接币种。
 *
 * 用法：npm run check:cost
 */
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { loadTsModule, projectRoot } from './_tsm.mjs'
import { createRequire } from 'node:module'

const cases = []
function check(name, fn) {
  try {
    fn()
    cases.push({ name, ok: true })
  } catch (e) {
    cases.push({ name, ok: false, error: e.message })
  }
}
async function checkAsync(name, fn) {
  try {
    await fn()
    cases.push({ name, ok: true })
  } catch (e) {
    cases.push({ name, ok: false, error: e.message })
  }
}

const { mod, cleanup } = await loadTsModule('src/utils/cost.ts')
const { formatCost, COST_SYMBOL, COST_CURRENCY, costCsvValue, reportedCost } = mod

// ---------- 口径本身 ----------
check('全站统一用美元符号和 USD 币种', () => {
  assert.equal(COST_SYMBOL, '$')
  assert.equal(COST_CURRENCY, 'USD')
  assert.equal(formatCost(0.0827), '$0.0827')
})

check('默认 4 位小数，够单次生成的量级（分/厘）显示', () => {
  assert.equal(formatCost(0.0001), '$0.0001')
  assert.equal(formatCost(1.5), '$1.5000')
})

check('聚合值可指定 2 位小数', () => {
  assert.equal(formatCost(12.3456, 2), '$12.35')
})

check('微小正金额在 2 位和默认精度下均保持非零', () => {
  assert.equal(formatCost(0.0000012, 2), '$0.0000012')
  assert.equal(formatCost(0.0000012), '$0.0000012')
  assert.equal(formatCost(1e-12, 2), '$1.0e-12')
})

check('缓存净节省可以表达负数且不塌成负零', () => {
  assert.equal(formatCost(-0.0000012, 2), '-$0.0000012')
  assert.equal(formatCost(-1.5), '-$1.5000')
})

check('0 不带无意义的尾随小数', () => {
  assert.equal(formatCost(0), '$0')
  assert.equal(formatCost(0, 2), '$0')
})

check('无数据出占位符，不冒充零美元', () => {
  assert.equal(formatCost(null), '—')
  assert.equal(formatCost(undefined), '—')
  assert.equal(formatCost(NaN), '—')
  assert.equal(formatCost(Infinity), '—')
})

check('usage 缺失的零成本保持未知，真实零与已知部分成本仍显示', () => {
  assert.equal(reportedCost(0, 0, 2), null)
  assert.equal(reportedCost(0, 2, 2), 0)
  assert.equal(reportedCost(0, 0, 0), 0)
  assert.equal(reportedCost(0.02, 1, 2), 0.02)
  assert.equal(reportedCost(undefined, 2, 2), null)
})

check('CSV 保持纯数值和微小金额精度，未知为空', () => {
  assert.equal(costCsvValue(0.00000012), '1.2e-7')
  assert.equal(costCsvValue(1.23456789), '1.23456789')
  assert.equal(costCsvValue(0), '0')
  assert.equal(costCsvValue(null), '')
  assert.equal(costCsvValue(undefined), '')
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

// 直接加载用量展示的纯函数，验证累计与执行范围及缓存分母。
let usageModule
try { usageModule = await loadTsModule('src/utils/usageDisplay.ts') } catch { /* 新增契约缺失由断言报错 */ }
check('需求累计优先于本次执行；旧接口回退时明确本次执行', () => {
  assert.ok(usageModule, '缺少用量展示实现')
  const { selectRequirementUsage } = usageModule.mod
  const summary = { tokens: 150, cost: 0.1, llm_calls: 3 }
  const trace = { total_tokens: 40, total_cost: 0.02 }
  assert.deepEqual(selectRequirementUsage(summary, trace), { scope: '需求累计', usage: summary })
  assert.deepEqual(selectRequirementUsage(null, trace), {
    scope: '本次执行', usage: { ...trace, tokens: 40, cost: 0.02, tokens_in: undefined },
  })
  assert.equal(selectRequirementUsage(null, null).usage.cost, undefined)
})
check('缓存命中分母仅包含缓存已上报输入，并保留部分覆盖与真实零', () => {
  assert.ok(usageModule, '缺少用量展示实现')
  const { cacheUsageTitle, formatUsagePercent } = usageModule.mod
  const title = cacheUsageTitle({ cached_tokens: 100, cache_reported_tokens_in: 200,
    tokens_in: 1000, cache_coverage: 0.2, cache_reported_calls: 2 })
  assert.match(title, /100 \/ 200 个已上报缓存的输入 token/)
  assert.match(title, /20.0%.*200 \/ 1,000/)
  assert.equal(formatUsagePercent(0), '0.0%')
  assert.equal(formatUsagePercent(null), '未上报')
  assert.match(cacheUsageTitle({ tokens_in: 1000 }), /未上报/)
})
check('缓存净节省提示仅统计已知样本并标注样本数', () => {
  assert.ok(usageModule, '缺少用量展示实现')
  const { cacheSavingsTitle } = usageModule.mod
  assert.equal(cacheSavingsTitle({ cache_savings_reported_calls: 2, llm_calls: 5 }),
    '2 / 5 次调用具有已知缓存净节省；仅汇总已知样本，未知样本未计入')
  assert.match(cacheSavingsTitle({}), /— \/ — 次调用.*未知样本未计入/)
})

// 使用项目已安装的 Vue 编译器渲染真实 SFC；无需 DOM 或新增测试依赖。
await checkAsync('TokenBar 实际渲染累计快照、本次执行、覆盖率与非零微小金额', async () => {
  assert.ok(usageModule, '缺少用量展示实现')
  const require = createRequire(join(projectRoot, 'package.json'))
  const vue = require('vue')
  const { renderToString } = require('vue/server-renderer')
  const { parse, compileScript } = require('@vue/compiler-sfc')
  const ts = require('typescript')
  const source = readFileSync(join(projectRoot, 'src/components/detail/TokenBar.vue'), 'utf8')
  const { descriptor } = parse(source)
  const script = compileScript(descriptor, { id: 'cost-check', inlineTemplate: true })
  const compiled = ts.transpileModule(script.content, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText
  const exports = {}
  new Function('require', 'exports', compiled)((id) => {
    if (id === 'vue') return vue
    if (id === '@/utils/cost') return mod
    if (id === '@/utils/usageDisplay') return usageModule.mod
    throw new Error(`未处理的组件依赖：${id}`)
  }, exports)
  const render = (props) => renderToString(vue.createSSRApp(exports.default, props))
  const html = await render({ scope: '需求累计', summaryStale: true,
    tokens: 0, cost: 0, usageReportedCalls: 2, llmCalls: 2,
    cacheHitRate: 0, cacheCoverage: 0.5, cachedTokens: 0,
    inputTokens: 1000, cacheReportedTokensIn: 500, cacheReportedCalls: 1,
    executionTokens: 42, executionCost: 0.0000012, cacheSavings: 0,
    cacheSavingsReportedCalls: 1 })
  const text = html.replace(/<[^>]*>/g, '')
  assert.match(text, /需求累计.*快照待刷新/)
  assert.match(text, /tokens 0/)
  assert.match(text, /估算成本 USD \$0/)
  assert.match(text, /缓存命中 0.0%.*输入覆盖率 50.0%/)
  assert.match(html, /0 \/ 500 个已上报缓存的输入 token/)
  assert.match(text, /本次执行快照 42 tokens.*\$0.0000012/)
  assert.match(text, /已知样本缓存净节省 USD \$0/)
  assert.match(html, /1 \/ 2 次调用具有已知缓存净节省.*未知样本未计入/)
  const unknown = (await render({ scope: '本次执行', cost: 0, tokens: 0,
    usageReportedCalls: 0, llmCalls: 2 })).replace(/<[^>]*>/g, '')
  assert.match(unknown, /tokens —/)
  assert.match(unknown, /估算成本 USD —/)
  assert.match(unknown, /缓存命中 未上报.*输入覆盖率 未上报/)
  assert.doesNotMatch(unknown, /已知样本缓存净节省/)
})
usageModule?.cleanup()

const failed = cases.filter((c) => !c.ok)
for (const c of cases) {
  console.log(`${c.ok ? '✅' : '❌'} ${c.name}${c.ok ? '' : `\n     ${c.error}`}`)
}
console.log(`\n${cases.length - failed.length}/${cases.length} 通过`)
process.exit(failed.length ? 1 : 0)
