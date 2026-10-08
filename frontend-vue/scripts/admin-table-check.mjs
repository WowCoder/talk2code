#!/usr/bin/env node
/**
 * 后台「需求轨迹」列表的三条结构性守卫。
 *
 * 为什么要有这个脚本：这里的事故都是**不报错、只是看着怪**的那类。
 *  1. 表头与单元格列数不一致 —— 新增一列时忘了删旧表头（或反之），
 *     浏览器不报错，只是从第 N 列起「标题和数据整体错位一格」：
 *     成本列顶着「最近活跃」的标题、操作按钮挤到最后。肉眼扫一眼很难发现。
 *  2. 时间范围档位缺失 —— 筛选条少一档，只有「用户想选却选不到」时才会暴露。
 *  3. 默认排序漂移 —— 默认改回「最近活跃」的话，跑着的需求会不停把自己顶到
 *     第一行，正在看的表会自己重排（用户反馈的原话是「排序很乱」）。
 *
 * 用法：npm run check:admin-table   （在 frontend-vue 目录下）
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(
  join(here, '..', 'src', 'views', 'admin', 'AdminTracesView.vue'), 'utf8')

let checks = 0
const ok = (cond, msg) => {
  checks++
  assert.ok(cond, `✗ ${msg}`)
  console.log(`  ✓ ${msg}`)
}

const slice = (tag) => {
  const open = src.indexOf(`<${tag}`)
  const close = src.indexOf(`</${tag}>`)
  assert.ok(open >= 0 && close > open, `模板里找不到 <${tag}>`)
  return src.slice(open, close)
}

// ---- 1. 表头列数 == 单元格列数 ----
const thead = slice('thead')
const thCount = (thead.match(/<th[\s>]/g) || []).length

// 取 tbody 里的第一行：v-for 的那一行就是每行单元格的真实形状
const tbody = slice('tbody')
const firstRowStart = tbody.indexOf('<tr')
const rowEnd = tbody.indexOf('</tr>', firstRowStart)
const firstRow = tbody.slice(firstRowStart, rowEnd)
const tdCount = (firstRow.match(/<td[\s>]/g) || []).length

ok(thCount > 0 && tdCount > 0, `解析到表头 ${thCount} 列 / 单元格 ${tdCount} 列`)
ok(thCount === tdCount,
   `表头与单元格列数一致（${thCount}）`)

// ---- 2. 时间范围档位齐全 ----
const rangeBlock = src.slice(src.indexOf('range-box'), src.indexOf('</select>'))
const values = [...rangeBlock.matchAll(/<option\s+value="([^"]+)"/g)].map(m => m[1])
ok(JSON.stringify(values) === JSON.stringify(['all', 'today', '3', '7', '30']),
   `时间范围五档齐全：${values.join(' / ')}`)

// ---- 3. 默认排序是创建时间 ----
const sortDefault = /const sortKey = ref<[^>]*>\('(\w+)'\)/.exec(src)
ok(sortDefault && sortDefault[1] === 'created',
   `默认按创建时间倒序（当前 ${sortDefault ? sortDefault[1] : '未解析到'}）`)
ok(/pickSort\('active'\)/.test(src) && /pickSort\('created'\)/.test(src),
   '两列时间都可点排序')

console.log(`\n后台列表守卫通过：${checks} 项`)
