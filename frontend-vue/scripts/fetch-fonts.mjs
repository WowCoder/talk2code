#!/usr/bin/env node
/**
 * 把 Google Fonts 的拉丁字体下载到本地自托管，并生成 src/assets/styles/fonts.css。
 *
 * 为什么要自托管：index.html 原来外链 fonts.googleapis.com —— 那是一张
 * **阻塞渲染的跨域样式表**。在网络到不了 Google 的环境（大陆最常见）下，
 * 浏览器会卡在这个请求上直到超时，首屏长时间白屏。自托管后整站不再依赖
 * 任何外部 origin。
 *
 * 为什么只自托管拉丁字体（Newsreader / JetBrains Mono）、中文走系统字体栈：
 * 中文整包 4.4 MB，即便按 unicode-range 分片，一个中文页面仍要拉 13~16 片、
 * 368~783 KB —— 占页面总传输量的 82%~91%，且分片是随渲染逐批发现的，在弱网下
 * 会把路由 JS 和后端接口全部堵在连接里。改用系统字体后这一项归零：
 * macOS/iOS 落 PingFang SC、Windows 落微软雅黑、Android 落 Noto Sans CJK，
 * 均为现代黑体，观感一致。中文站（淘宝/知乎/B站）都不下发中文 webfont。
 * 拉丁字体需要跨平台字形一致（品牌衬线标题、代码等宽），所以继续自托管。
 *
 * 为什么保留 @font-face + unicode-range 分片：浏览器**只下载页面实际用到的
 * 分片**，不必整包下载。
 *
 * 为什么能合并字重：两族都是**可变字体**（同一 woff2 承载 400/500/600/700），
 * 所以每个分片只需一条 `font-weight: 400 700` 的声明。
 *
 * 为什么 Newsreader 不带 opsz 轴：带上 `opsz` 那版的 latin 分片是 128.8 KB，
 * 只请求 `wght` 轴的同族同分片只有 56.8 KB（-56%）。opsz 只是按字号微调笔画
 * 对比度，丢掉后渲染差异肉眼不可辨，不值得为它多传 72 KB。
 *
 * 用法：cd frontend-vue && npm run fonts
 * 产物：public/fonts/*.woff2（入库）+ src/assets/styles/fonts.css
 */
import { mkdir, readdir, unlink, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const OUT_FONT_DIR = path.join(ROOT, 'public', 'fonts')
const OUT_CSS = path.join(ROOT, 'src', 'assets', 'styles', 'fonts.css')

// 只保留拉丁两族。中文不再自托管，走系统字体栈（见文件头说明）。
const CSS_URL =
  'https://fonts.googleapis.com/css2' +
  '?family=JetBrains+Mono:ital,wght@0,400;0,500;0,700;1,400' +
  '&family=Newsreader:wght@400..700' +
  '&display=swap'

// Google 只对「声明支持 woff2 的浏览器」返回 woff2 版 CSS，否则回落到 ttf。必须伪装 UA。
const UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

const FAMILY_ORDER = ['JetBrains Mono', 'Newsreader']

/**
 * 把声明的字重压成一条 descriptor。
 *
 * 判据：**同一个 URL 被声明了多个字重** ⇒ 那是可变字体。Google 只对可变字体这么发
 * （静态字重每个字重都会有各自的 URL）。此时声明成区间 `400 700` 才合法 ——
 * CSS 的 @font-face `font-weight` 不接受逗号列表，写 `400, 500, 600, 700` 会让整条
 * 声明失效回落成 normal，字重全部丢失。
 *
 * 反例：JetBrains Mono 的斜体在 Google 上就是**静态 400**，只被声明一次，保持单值。
 *
 * ⚠️ 入参可能是单值（`'400'`）也可能是**已经是区间的串**（`'400 700'`）—— 后者出现在
 * 我们用 `wght@400..700` 请求可变字体时，Google 直接回区间。若把它当列表逐项 Number()
 * 会得到 NaN，生成 `font-weight: NaN` 这种非法声明被浏览器整条丢弃、粗体退化成伪粗体，
 * 且不报任何错。所以这里按「每个 token 解析成 [lo, hi]」再取全局极值。
 */
function toWeightDescriptor(weights) {
  const bounds = weights.map((w) => {
    const nums = String(w).trim().split(/\s+/).map(Number)
    if (!nums.length || nums.some((n) => !Number.isFinite(n))) {
      throw new Error(`无法解析字重声明：${JSON.stringify(w)}`)
    }
    return [nums[0], nums[nums.length - 1]]
  })
  const lo = Math.min(...bounds.map((b) => b[0]))
  const hi = Math.max(...bounds.map((b) => b[1]))
  return lo === hi ? String(lo) : `${lo} ${hi}`
}

/** unicode-range 列表很长，按逗号折行，避免单行几千字符 */
function wrapUnicodeRange(value) {
  const parts = value.split(',').map((s) => s.trim())
  const lines = []
  let cur = ''
  for (const p of parts) {
    const next = cur ? `${cur}, ${p}` : p
    if (next.length > 96 && cur) {
      lines.push(`${cur},`)
      cur = p
    } else {
      cur = next
    }
  }
  if (cur) lines.push(cur)
  return `${lines.map((l, i) => (i === 0 ? `  unicode-range: ${l}` : `    ${l}`)).join('\n')};`
}

async function main() {
  process.stdout.write(`拉取 Google Fonts CSS…\n`)
  const resp = await fetch(CSS_URL, { headers: { 'User-Agent': UA } })
  if (!resp.ok) throw new Error(`CSS 请求失败: HTTP ${resp.status}`)
  const css = await resp.text()

  const blocks = [...css.matchAll(/@font-face\s*\{([\s\S]*?)\}/g)].map((m) => m[1])
  if (!blocks.length) throw new Error('CSS 里没有解析到 @font-face，疑似 Google 返回了错误页')

  // 同一分片（同 URL）会被声明多次（每个字重一条），合并成一条
  const merged = new Map() // key: family|style|url
  for (const [idx, block] of blocks.entries()) {
    const family = /font-family:\s*'([^']+)'/.exec(block)?.[1]
    const style = /font-style:\s*([^;]+);/.exec(block)?.[1].trim() ?? 'normal'
    const weight = /font-weight:\s*([^;]+);/.exec(block)?.[1].trim()
    const range = /unicode-range:\s*([^;]+);/.exec(block)?.[1].trim()
    const url = /url\((https:\/\/[^)]+\.woff2)\)/.exec(block)?.[1]
    if (!family || !weight || !url) throw new Error(`第 ${idx} 个 @font-face 缺少必要字段`)
    const key = `${family}|${style}|${url}`
    if (!merged.has(key)) {
      merged.set(key, { family, style, url, weights: [], range, idx })
    }
    merged.get(key).weights.push(weight)
  }

  const entries = [...merged.values()].sort((a, b) => {
    const fa = FAMILY_ORDER.indexOf(a.family)
    const fb = FAMILY_ORDER.indexOf(b.family)
    if (fa !== fb) return fa - fb
    if (a.style !== b.style) return a.style === 'normal' ? -1 : 1
    if (Math.min(...a.weights) !== Math.min(...b.weights)) {
      return Math.min(...a.weights) - Math.min(...b.weights)
    }
    return a.idx - b.idx
  })

  await mkdir(OUT_FONT_DIR, { recursive: true })
  process.stdout.write(`下载 ${entries.length} 个 woff2 → public/fonts/\n`)

  const names = new Set()
  let done = 0
  const CONCURRENCY = 12
  for (let i = 0; i < entries.length; i += CONCURRENCY) {
    await Promise.all(
      entries.slice(i, i + CONCURRENCY).map(async (e) => {
        let name = e.url.split('/').pop()
        // 跨字族理论上可能重名；真撞了就加上字族前缀
        if (names.has(name)) name = `${e.family.toLowerCase().replace(/\s+/g, '-')}-${name}`
        names.add(name)
        e.file = name
        const r = await fetch(e.url, { headers: { 'User-Agent': UA } })
        if (!r.ok) throw new Error(`字体下载失败 ${e.url}: HTTP ${r.status}`)
        await writeFile(path.join(OUT_FONT_DIR, name), Buffer.from(await r.arrayBuffer()))
        done += 1
        if (done % 20 === 0) process.stdout.write(`  …${done}/${entries.length}\n`)
      }),
    )
  }

  const header = [
    '/* 自动生成，请勿手改。重新生成：cd frontend-vue && npm run fonts',
    ' *',
    ' * 只含拉丁两族（Newsreader / JetBrains Mono）。中文走系统字体栈，',
    ' * 不下发中文 webfont —— 详见 frontend-vue/scripts/fetch-fonts.mjs。',
    ' * 保留 unicode-range 分片：浏览器只下载页面实际用到的字形分片。',
    ' * 两族均为可变字体，故一个字重区间即可覆盖 400/500/600/700。',
    ' */',
    '',
  ].join('\n')

  const body = entries
    .map((e) =>
      [
        '@font-face {',
        `  font-family: '${e.family}';`,
        `  font-style: ${e.style};`,
        `  font-weight: ${toWeightDescriptor(e.weights)};`,
        '  font-display: swap;',
        `  src: url('/fonts/${e.file}') format('woff2');`,
        wrapUnicodeRange(e.range),
        '}',
      ].join('\n'),
    )
    .join('\n\n')

  await writeFile(OUT_CSS, `${header}${body}\n`, 'utf8')

  // 生成物自检：非法值不会让构建失败，只会让整条 @font-face 被浏览器静默丢弃
  // （字重回退、unicode-range 丢失），必须在写盘时就拦住。
  const out = `${header}${body}\n`
  for (const bad of ['NaN', 'undefined', 'null']) {
    if (out.includes(bad)) throw new Error(`生成物里出现 ${bad}，@font-face 会被浏览器丢弃`)
  }
  const written = [...out.matchAll(/@font-face\s*\{([\s\S]*?)\}/g)]
  if (written.length !== entries.length) {
    throw new Error(`写出的 @font-face 数（${written.length}）与预期（${entries.length}）不符`)
  }
  for (const [, blk] of written) {
    if (!/font-weight:\s*\d+(\s+\d+)?;/.test(blk)) {
      throw new Error(`@font-face 缺少合法 font-weight：${blk.slice(0, 60)}`)
    }
    if (!/unicode-range:\s*U\+/.test(blk)) {
      throw new Error(`@font-face 缺少 unicode-range：${blk.slice(0, 60)}`)
    }
  }

  process.stdout.write(`已生成 ${path.relative(ROOT, OUT_CSS)}（${entries.length} 条 @font-face）\n`)

  // 清理上一代产物：改字体族配置（如去掉中文字族）后，旧分片会变成没人引用的
  // 孤儿文件，继续留在 public/fonts/ 里既占仓库又会被部署进 dist。
  const stale = (await readdir(OUT_FONT_DIR)).filter((f) => !names.has(f))
  for (const f of stale) await unlink(path.join(OUT_FONT_DIR, f))
  if (stale.length) process.stdout.write(`已清理 ${stale.length} 个孤儿字体文件\n`)
}

main().catch((err) => {
  process.stderr.write(`失败：${err.message}\n`)
  process.exit(1)
})
