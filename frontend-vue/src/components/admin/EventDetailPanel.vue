<template>
  <section class="col col-detail">
    <div class="col-head">
      <span class="col-title">事件详情</span>
      <button v-if="detail" class="copy-btn" @click="copyJson">
        {{ copyLabel('json', '复制 JSON') }}
      </button>
    </div>
    <div class="col-body dark-body">
      <div v-if="loading" class="dark-empty">加载中…</div>
      <div v-else-if="!detail" class="dark-empty">
        从左侧选择任意事件<br />查看完整输入与输出
      </div>
      <template v-else>
        <!-- 概览条 -->
        <div class="d-head">
          <div class="d-title">{{ detail.label || detail.kind }}</div>
          <div class="d-meta">
            <!-- 分隔符跟着**有值的**字段走。之前把 " · " 写在条件块里，
                 里程碑事件（没有 model）就会留下一句结尾的「9s ·」——
                 看着像后面还该有个东西没渲染出来。 -->
            <template v-for="(p, i) in headParts" :key="i">
              <template v-if="i"> · </template>{{ p }}
            </template>
            <!-- call_id 是 llm_traffic.log 的索引键：点一下复制，就能直接去传输层
                 捞这次请求的原始报文。只显示前 8 位防换行，title 与复制内容是完整的。 -->
            <span v-if="detail.call_id" class="cid"
                  :title="`点击复制 call_id（完整：${detail.call_id}），可在 llm_traffic.log 里定位这次请求的原始报文`"
                  @click="copy(detail.call_id, 'cid')"> · call_id {{ detail.call_id.slice(0, 8) }}<template v-if="copyState['cid'] === 'ok'"> 已复制</template></span>
          </div>
          <div class="d-params">
            <span v-if="detail.messages.length" class="p-item">
              messages <b>{{ detail.messages.length }}</b>
            </span>
            <span v-if="detail.tokens_in || detail.tokens_out" class="p-item">
              tokens <b>{{ detail.tokens_in }}/{{ detail.tokens_out }}</b>
            </span>
            <span v-if="detail.tools" class="p-item">
              tools <b>{{ detail.tools.length }}</b>
            </span>
          </div>
        </div>

        <!-- ===== 分页签 =====
             单签时不渲染签行 —— 只有一块内容还摆一排 tab 是噪声。
             签上带计数：不点进去也知道这块有多大。 -->
        <div v-if="detailTabs.length > 1" class="d-tabs">
          <button v-for="t in detailTabs" :key="t.key" class="d-tab"
                  :class="{ on: activeTab === t.key }" @click="activeTab = t.key">
            {{ t.label }}<span v-if="t.count != null" class="d-tab-n">{{ t.count }}</span>
          </button>
        </div>

        <!-- 要点：里程碑事件写入的 meta（判定 / 得分 / 未达成 AC / 修复轮次…）。
             不渲染它，这些字段就只躺在库里 —— 界面上「验收为什么没过」没有答案，
             排查还得回去翻 DB，等于埋点白做。 -->
        <template v-if="activeTab === 'overview' && metaRows.length">
          <div class="d-section">要点</div>
          <div class="kf">
            <div v-for="row in metaRows" :key="row.k" class="kf-row">
              <span class="kf-k" :title="row.k">{{ row.label }}</span>
              <!-- title 挂完整值：一行里被 clip 过的明细，悬停仍能看到全文 -->
              <span class="kf-v" :class="{ alert: row.alert }" :title="row.v">{{ row.v }}</span>
            </div>
          </div>
        </template>

        <!-- ===== REQUEST / 入参 ===== -->
        <template v-if="activeTab === 'request'">
          <!-- 工具行：条数 + 批量展开开关。
               旧版 MSG_PAGE=3 的分页已删 —— 排查时「默认只出 3 条」等于默认是瞎的；
               改为全量渲染，超长的单条仍默认折叠，批量开关一次全开/全收。 -->
          <div v-if="detail.messages.length || detail.tools" class="req-bar">
            <span class="req-note">
              <template v-if="detail.messages.length">{{ detail.messages.length }} 条 message</template>
              <template v-if="detail.messages.length && detail.tools"> · </template>
              <template v-if="detail.tools">{{ detail.tools.length }} 项工具定义</template>
            </span>
            <button v-if="longKeys.length" class="req-all" @click="toggleAllPrompts">
              {{ allLongExpanded
                ? '收起全部 prompt'
                : `展开全部 prompt（${longKeys.length} 段超长）` }}
            </button>
          </div>

          <div v-for="m in detail.messages" :key="m.index" class="msg">
            <div class="msg-bar">
              <span class="role-badge" :style="{ background: roleColor(m.role) }">{{ m.role }}</span>
              <span v-if="m.name" class="msg-tag">{{ m.name }}</span>
              <span v-if="m.missing" class="msg-missing">正文缺失</span>
              <span v-else class="msg-len">{{ fmtCount(m.char_len) }} chars</span>
              <button v-if="!m.missing" class="msg-copy" @click.stop="copy(m.content ?? '', 'msg' + m.index)">{{ copyLabel('msg' + m.index, '复制') }}</button>
            </div>
            <pre v-if="m.missing" class="msg-body msg-gap">
这条 message 的正文没有存下来（blob 缺失），不是它本来为空。</pre>
            <pre v-else class="msg-body" :class="{ clipped: !expanded.has('m' + m.index) }">{{ m.content }}</pre>
            <button v-if="!m.missing && m.char_len > CLIP_LEN" class="more-btn" @click.stop="toggleMsg('m' + m.index)">
              {{ expanded.has('m' + m.index) ? '收起' : `展开全部 ${fmtCount(m.char_len)} 字符` }}
            </button>
          </div>

          <!-- 工具入参（tool_call）：结构化渲染，不再整块 JSON。
               arguments 超长落库走内容寻址，后端会额外还原出 arguments_full ——
               有它才看得到 write_file 写入的完整文件（旧版只显示 2000 字符截断预览）。 -->
          <template v-if="toolArgs.length">
            <div v-for="a in toolArgs" :key="a.key" class="ta">
              <div class="ta-head">
                <span class="kf-k" :title="a.key">{{ a.label }}</span>
                <span v-if="a.full" class="ta-flag"
                      title="参数超过 2000 字符时落库只存预览 + hash，此处是按 hash 还原出的完整原文">完整内容 · 预览已截断</span>
              </div>
              <pre v-if="a.long" class="msg-body" :class="{ clipped: !expanded.has(a.taKey) }">{{ a.value }}</pre>
              <div v-else class="ta-short">{{ a.value }}</div>
              <button v-if="a.long" class="more-btn" @click.stop="toggleMsg(a.taKey)">
                {{ expanded.has(a.taKey) ? '收起' : `展开全部 ${fmtCount(a.value.length)} 字符` }}
              </button>
            </div>
          </template>
          <div v-else-if="hasArgs" class="msg">
            <pre class="msg-body">{{ argsJson }}</pre>
          </div>

          <div v-if="detail.tools" class="msg">
            <div class="msg-bar">
              <span class="role-badge tools-b">tools</span>
              <span class="msg-len">{{ detail.tools.length }} 项工具定义</span>
            </div>
            <button class="more-btn" @click.stop="toggleMsg('tools')">
              {{ expanded.has('tools') ? '收起' : '查看工具定义' }}
            </button>
            <!-- 展开后不再 clipped：旧版硬编码 clipped，4k 字符的定义永远只看到开头 -->
            <pre v-if="expanded.has('tools')" class="msg-body">{{ toolsJson }}</pre>
          </div>
        </template>

        <!-- ===== RESPONSE / 结果 ===== -->
        <template v-if="activeTab === 'response'">
          <template v-if="detail.response">
            <div v-if="detail.response.content" class="msg">
              <div class="msg-bar">
                <span class="role-badge assistant-b">assistant</span>
                <span class="msg-len">{{ fmtCount(String(detail.response.content).length) }} chars</span>
                <button class="msg-copy" @click.stop="copy(detail.response.content, 'resp')">{{ copyLabel('resp', '复制') }}</button>
              </div>
              <pre class="msg-body" :class="{ clipped: !expanded.has('resp') }">{{ detail.response.content }}</pre>
              <button v-if="String(detail.response.content).length > CLIP_LEN"
                      class="more-btn" @click.stop="toggleMsg('resp')">
                {{ expanded.has('resp') ? '收起' : '展开全部' }}
              </button>
            </div>
            <div v-if="detail.response.tool_calls && detail.response.tool_calls.length" class="msg">
              <div class="msg-bar">
                <span class="role-badge tools-b">tool_calls</span>
                <span class="msg-len">{{ detail.response.tool_calls.length }} 次调用</span>
              </div>
              <div v-for="(tc, i) in detail.response.tool_calls" :key="i" class="tc">
                <div class="tc-name">{{ tc.function?.name ?? tc.name ?? 'unknown' }}</div>
                <pre class="tc-args">{{ tc.function?.arguments ?? tc.arguments ?? '' }}</pre>
              </div>
            </div>
          </template>

          <div v-if="detail.tool_content" class="msg">
            <div class="d-section">TOOL RESULT</div>
            <pre class="msg-body" :class="{ clipped: !expanded.has('tc') }">{{ detail.tool_content }}</pre>
            <button v-if="detail.tool_content.length > CLIP_LEN" class="more-btn" @click.stop="toggleMsg('tc')">
              {{ expanded.has('tc') ? '收起' : `展开全部 ${fmtCount(detail.tool_content.length)} 字符` }}
            </button>
          </div>
        </template>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * 事件详情（右栏）—— 需求轨迹页与评测页共用。
 *
 * 这里承载的是「一次 LLM 调用到底发了什么、回了什么」：要点（里程碑 meta）、
 * Request（messages / tools / 工具入参）、Response。两处各写一份的话，
 * 「验收为什么没过」这类结论字段很容易只在一边被渲染出来。
 */
import { computed, ref, watch } from 'vue'

import type { Detail, DetailTab, ToolCall } from '@/types/trace'
import { fmtClock, fmtCount, fmtDuration } from '@/utils/format'

const props = defineProps<{
  detail: Detail | null
  loading?: boolean
}>()

const CLIP_LEN = 700

const activeTab = ref('overview')
const expanded = ref<Set<string>>(new Set())
// 复制按钮的瞬时状态：{ [按钮 key]: 'ok' | 'fail' }。复制是「按了没有任何视觉变化」
// 的动作，没有这个状态就分不清「复制成功」和「按钮没反应」。
const copyState = ref<Record<string, 'ok' | 'fail'>>({})

// 换事件时展开状态与当前签都要重置 —— 上一条事件里点开的 prompt 不该默默
// 作用于下一条（长文本会以为没折叠，短文本会以为点了没反应）
watch(() => props.detail, () => {
  expanded.value = new Set()
  activeTab.value = 'overview'
})

type _ToolCall = ToolCall   // 类型已在模板推导中用到，保留导入以免被 tree-shake 误判

/** 概览条：时间 / 耗时 / 模型 —— 有空值的字段直接不参与，不留悬空分隔符 */
const headParts = computed<string[]>(() => {
  const d = props.detail
  if (!d) return []
  const out = [fmtClock(d.ts)]
  if (d.duration_ms) out.push(fmtDuration(d.duration_ms))
  if (d.model) out.push(d.model)
  return out
})

const detailTabs = computed<DetailTab[]>(() => {
  const d = props.detail
  if (!d) return []
  const tabs: DetailTab[] = []
  if (metaRows.value.length) tabs.push({ key: 'overview', label: '要点' })
  if (d.kind === 'tool_call') {
    if (hasArgs.value) tabs.push({ key: 'request', label: '入参' })
    if (d.tool_content) tabs.push({ key: 'response', label: '结果' })
  } else {
    // llm_turn 与里程碑：Request = 喂进去的 prompt（+工具定义），
    // Response = 模型产出。里程碑事件补埋点后 messages/response 会有内容，
    // 没补到的事件两个签都不出现，只剩「要点」。
    if (d.messages.length || d.tools || hasArgs.value) {
      tabs.push({ key: 'request', label: 'Request', count: d.messages.length || undefined })
    }
    if (d.response || d.tool_content) tabs.push({ key: 'response', label: 'Response' })
  }
  return tabs
})

// 详情切换后当前签可能已不存在（如 tool_call 没有「要点」），
// 归位到第一个可用签，避免渲染出一块空白面板。
watch(detailTabs, tabs => {
  if (!tabs.some(t => t.key === activeTab.value)) {
    activeTab.value = tabs[0]?.key ?? 'overview'
  }
}, { immediate: true })

const ROLE_COLOR: Record<string, string> = {
  system: 'oklch(48% 0.14 250)',
  user: 'oklch(50% 0.13 150)',
  assistant: 'oklch(48% 0.15 300)',
  tool: 'oklch(58% 0.13 70)',
}
const roleColor = (r: string) => ROLE_COLOR[r] ?? 'oklch(55% 0.02 70)'

// ---- 结论要点（meta 的中文化渲染） ----
// 键 → 人话。新增埋点时在这里补一行；没登记的键显示原名而不是隐藏 ——
// 隐藏会让人分不清「后端没写」和「前端不认识」。
const META_LABEL: Record<string, string> = {
  verdict: '判定', score: '得分', fast_pass: '快速通过',
  findings: '问题总数', critical_count: '严重问题', defect_count: '缺陷数',
  failed_ac_ids: '未达成验收项', ac_total: '验收项总数',
  round: '修复轮次', target_defects: '目标缺陷', written_files: '写入文件',
  features: '功能点', ac_count: '验收项数', complexity: '复杂度',
  dod_issues: 'DoD 问题', reason: '原因', question_count: '待答问题',
  file_count: '文件数', files: '文件', intent: '意图', confidence: '置信度',
  skill_name: '技能', has_feedback: '附修改意见', feedback_len: '意见字数',
  blocked: '已拦截', unmet_acs: '未达成 AC', unmet: '未达成',
  repair_rounds: '修复轮数',
  gate: '闸门', restored_files: '已恢复文件', removed_files: '已删除文件',
  summary: '摘要', injected: '注入条数', hit_ids: '命中记忆', hit: '命中条数',
  error: '错误', message_count: '消息数', content_ref: '内容指纹',
  thinking: '含思考', has_usage: '含用量', source: '来源',
  // ---- B2 补埋点后新增的明细字段 ----
  questions: '问题列表', feature_list: '功能清单', ac_list: '验收项明细',
  plan_issues: '规划问题', items: '注入明细', feedback: '用户反馈',
  confirmed_features: '确认的功能', confirmed_acs: '确认的验收项',
}
const ALERT_KEYS = new Set(['critical_count', 'failed_ac_ids', 'defect_count',
  'target_defects', 'error', 'dod_issues', 'plan_issues', 'unmet'])
// 过程性字段的「默认值」：等于默认值就不显示。全渲染会让「要点」退化成噪声
// （每个 tool_call 都顶一行「已拦截 否」），排查时反而看不见真正的结论。
// 反过来说，blocked=true、thinking=false 这类**偏离默认**的值会照常出现。
const NEUTRAL_META: Record<string, unknown> = {
  blocked: false, thinking: false, has_usage: true,
}
// 纯内部标识，对排查没有信息量（正文本身就在下面）
const HIDDEN_META = new Set(['content_ref', 'message_count'])

// 单条明细在一行里最多显示多少字。记忆教训后端存 200 字，不截会把「要点」
// 撑成一大坨，反而看不见判定/得分这些真正要一眼看到的结论。
const ITEM_TEXT_CAP = 100
const clip = (s: string, n = ITEM_TEXT_CAP) =>
  s.length > n ? `${s.slice(0, n)}…` : s

const fmtMetaValue = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (Array.isArray(v)) {
    if (!v.length) return '无'
    const head = v.slice(0, 6).map(x => {
      if (typeof x === 'object' && x !== null) {
        // 对象条目优先念人话字段（澄清问题的 label、记忆条目的 lesson），
        // JSON 一行展开在要点区没法读。
        //
        // ⚠️ 记忆条目必须念 `lesson`（教训正文）而不是 `requirement`（来源需求）：
        // 只念来源需求时，时间线上看到的是一条和自己毫无关系的旧需求标题，
        // 看起来就像注入了噪音 —— 而真正被注入的那句教训一个字都没露出来。
        // 来源需求保留为「来源：…」后缀：它是判断这条教训是否适用的依据。
        const o = x as Record<string, unknown>
        const prefix = o.id ? `${o.id} ` : ''
        if (o.lesson) {
          const src = o.requirement ? `（来源：${clip(String(o.requirement), 40)}）` : ''
          return `${prefix}教训：${clip(String(o.lesson))}${src}`
        }
        const text = o.label ?? o.question ?? o.title ?? o.requirement
        if (text) return `${prefix}${clip(String(text))}`
        return clip(JSON.stringify(x))
      }
      return clip(String(x))
    })
    return v.length > 6 ? `${head.join('；')} 等 ${v.length} 项` : head.join('；')
  }
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
// 出现即代表「这次交付有麻烦」的字段标红：判定非 PASS、被拦截、有 critical…
const metaAlert = (k: string, v: unknown): boolean => {
  if (k === 'verdict') return String(v).toUpperCase() !== 'PASS'
  if (k === 'blocked') return v === true
  if (ALERT_KEYS.has(k)) return Array.isArray(v) ? v.length > 0 : !!v
  return false
}
const metaRows = computed(() => {
  const m = props.detail?.meta
  const rows = !m || typeof m !== 'object' ? [] : Object.entries(m)
    .filter(([k, v]) => !HIDDEN_META.has(k)
      && !(k in NEUTRAL_META && v === NEUTRAL_META[k]))
    .map(([k, v]) => ({
      k, label: META_LABEL[k] ?? k, v: fmtMetaValue(v), alert: metaAlert(k, v),
    }))
  // 大参数还原失败：此时工具参数是**残缺**的（只显示预览截断），
  // 不明说的话会被当成完整内容读 —— 这是唯一需要把 content_ref
  // 的下落讲清楚的场合，单独列一行标红。
  if (props.detail?.args_resolved === false) {
    rows.push({ k: 'args_resolved', label: '参数还原',
                v: '失败（正文缺失，参数只显示截断预览）', alert: true })
  }
  return rows
})

const toolsJson = computed(() => JSON.stringify(props.detail?.tools ?? [], null, 2))
const argsJson = computed(() => {
  const p = props.detail?.request_params
  return p ? JSON.stringify(p, null, 2) : ''
})
const hasArgs = computed(() => {
  const p = props.detail?.request_params
  return !!p && Object.keys(p).length > 0
})

// ---- 工具入参的结构化渲染 ----
// 旧版把 request_params 整块 JSON.stringify：name / arg_refs 是噪声，content
// 转义成一行挤在中间；更糟的是 arguments 超 2000 字符时落库只有截断预览，
// 完整内容在 arguments_full 里，旧版根本没显示 —— write_file 写了什么看不全。
const ARG_LABEL: Record<string, string> = {
  section: '写入小节', content: '内容', filename: '文件', path: '路径',
  old_string: '替换前', new_string: '替换后', command: '命令',
  query: '查询', url: '地址', code: '代码',
}
interface ToolArg {
  key: string; label: string; value: string
  /** 非 null = arguments 里是截断预览，value 已换成 arguments_full 的完整原文 */
  full: string | null
  long: boolean; taKey: string
}
const toolArgs = computed<ToolArg[]>(() => {
  const p = props.detail?.request_params
  if (!p) return []
  const raw = p.arguments
  if (!raw || typeof raw !== 'object') return []
  const args = raw as Record<string, unknown>
  const fullMap = (p.arguments_full ?? {}) as Record<string, unknown>
  return Object.entries(args).map(([k, v], i) => {
    const preview = typeof v === 'string' ? v : JSON.stringify(v, null, 2)
    const resolved = fullMap[k]
    const truncated = typeof resolved === 'string' && resolved !== preview
    const value = truncated ? resolved : preview
    return {
      key: k,
      label: ARG_LABEL[k] ?? k,
      value,
      full: truncated ? resolved : null,
      long: value.length > 220 || value.includes('\n'),
      taKey: `ta${i}`,
    }
  })
})

// ---- 批量展开 / 收起全部超长 prompt ----
// 长文本默认折叠是为了首屏可读；但排查时经常要看全所有输入 —— 逐条点太磨人。
// 收起/展开只作用于「超长」的条目（longKeys），短条目本来就没折叠，不受影响。
const longKeys = computed<string[]>(() => {
  const ks: string[] = []
  for (const m of props.detail?.messages ?? []) {
    if (!m.missing && m.char_len > CLIP_LEN) ks.push('m' + m.index)
  }
  if (String(props.detail?.response?.content ?? '').length > CLIP_LEN) ks.push('resp')
  if ((props.detail?.tool_content ?? '').length > CLIP_LEN) ks.push('tc')
  for (const a of toolArgs.value) if (a.long) ks.push(a.taKey)
  return ks
})
const allLongExpanded = computed(() =>
  longKeys.value.length > 0 && longKeys.value.every(k => expanded.value.has(k)))

function toggleAllPrompts() {
  if (allLongExpanded.value) {
    const keep = new Set([...expanded.value].filter(k => !longKeys.value.includes(k)))
    expanded.value = keep
  } else {
    expanded.value = new Set([...expanded.value, ...longKeys.value])
  }
}

function toggleMsg(k: string) {
  // key 一律显式传入（'m0' / 'resp' / 'tc' / 'tools' / 'ta0'）——
  // 旧版在这里做数字/字符串的猜测转换，'m-1' 这种 key 就是那么来的
  const s = new Set(expanded.value)
  s.has(k) ? s.delete(k) : s.add(k)
  expanded.value = s
}

async function copy(text: string, key: string) {
  let ok = true
  try { await navigator.clipboard.writeText(text) } catch { ok = false }
  // 静默吞掉异常时，用户按了按钮不知道有没有生效 —— 成功和失败都必须说出来
  copyState.value = { ...copyState.value, [key]: ok ? 'ok' : 'fail' }
  window.setTimeout(() => {
    const next = { ...copyState.value }
    delete next[key]
    copyState.value = next
  }, 1600)
}
function copyLabel(key: string, idle: string) {
  const st = copyState.value[key]
  return st === 'ok' ? '已复制' : st === 'fail' ? '复制失败' : idle
}
async function copyJson() {
  if (props.detail) await copy(JSON.stringify(props.detail, null, 2), 'json')
}
</script>

<style scoped>
.col {
  display: flex; flex-direction: column; min-width: 0;
  background: #fff; border: 1px solid oklch(90% 0.02 75); border-radius: 12px;
  overflow: hidden;
}
.col-detail { flex: 1; }
.col-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 11px 14px; border-bottom: 1px solid oklch(93% 0.02 75);
  background: oklch(98.5% 0.01 80); flex-shrink: 0;
}
.col-title { font-size: 12px; font-weight: 600; color: oklch(40% 0.02 60); }
.col-body { flex: 1; overflow-y: auto; padding: 8px; }

/* ---- 深色明细 ---- */
.dark-body { background: oklch(22% 0.012 65); padding: 0; }
.dark-empty {
  padding: 44px 20px; text-align: center; font-size: 12.5px;
  color: oklch(62% 0.02 70); line-height: 1.8;
}
.d-head { padding: 12px 13px; border-bottom: 1px solid oklch(30% 0.015 65); }
.d-title { font-size: 12.5px; font-weight: 600; color: oklch(93% 0.01 85); margin-bottom: 3px; }
.d-meta {
  font-size: 10.5px; color: oklch(66% 0.02 70);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.d-params { display: flex; gap: 7px; margin-top: 7px; flex-wrap: wrap; }
.cid { cursor: pointer; border-bottom: 1px dotted oklch(55% 0.02 70); }
.cid:hover { color: oklch(85% 0.06 70); }
.p-item {
  font-size: 10px; color: oklch(66% 0.02 70); background: oklch(28% 0.015 65);
  padding: 2px 7px; border-radius: 5px;
}
.p-item b { color: oklch(88% 0.03 75); font-weight: 600; }

/* ---- 结论要点 ---- */
.kf { padding: 2px 13px 10px; display: flex; flex-direction: column; gap: 4px; }
.kf-row { display: flex; gap: 8px; align-items: baseline; }
.kf-k { font-size: 10.5px; color: oklch(64% 0.02 70); flex-shrink: 0; min-width: 74px; }
.kf-v {
  font-size: 11.5px; color: oklch(90% 0.01 85); line-height: 1.6;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  word-break: break-word;
}
.kf-v.alert { color: oklch(74% 0.15 32); font-weight: 600; }
.d-section {
  padding: 9px 13px 5px; font-size: 10px; font-weight: 700; letter-spacing: .1em;
  color: oklch(62% 0.03 70); font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.msg { padding: 6px 13px 9px; }
.msg-bar { display: flex; align-items: center; gap: 7px; margin-bottom: 4px; }
.role-badge {
  font-size: 9.5px; font-weight: 600; color: #fff; padding: 1.5px 6px;
  border-radius: 4px; text-transform: uppercase; letter-spacing: .04em;
}
.tools-b { background: oklch(58% 0.13 70) !important; }
.assistant-b { background: oklch(48% 0.15 300) !important; }
.msg-len {
  font-size: 9.5px; color: oklch(58% 0.02 70);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
/* message 的附加来源（如系统每轮注入的工作区状态）—— 没有它，
   这些 message 长得和真实用户输入一模一样 */
.msg-tag {
  font-size: 9.5px; padding: 1px 5px; border-radius: 3px;
  background: oklch(94% 0.02 70); color: oklch(45% 0.04 70);
  border: 1px solid oklch(88% 0.02 70);
}
.msg-missing {
  font-size: 9.5px; padding: 1px 5px; border-radius: 3px;
  background: oklch(93% 0.06 40); color: oklch(48% 0.14 35);
}
.msg-gap { color: oklch(55% 0.05 40); font-style: italic; }
.msg-copy, .copy-btn {
  margin-left: auto; background: none; border: none; font-size: 10px;
  color: oklch(66% 0.05 60); cursor: pointer; font-family: inherit; padding: 0;
}
.msg-copy:hover, .copy-btn:hover { color: oklch(80% 0.06 70); }
.copy-btn {
  color: oklch(48% 0.02 65); font-size: 11px; margin-left: 0;
}
.msg-body {
  margin: 0; padding: 8px 10px; border-radius: 7px;
  background: oklch(27% 0.015 65); border: 1px solid oklch(33% 0.015 65);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px; line-height: 1.62; color: oklch(90% 0.012 85);
  white-space: pre-wrap; word-break: break-word;
}
.msg-body.clipped { max-height: 148px; overflow: hidden; }
.more-btn {
  margin-top: 5px; background: none; border: none; padding: 0;
  font-size: 10.5px; color: oklch(64% 0.08 55); cursor: pointer;
  font-family: inherit; text-decoration: underline;
}
.more-btn:hover { color: oklch(76% 0.1 55); }
.tc { padding: 5px 0 0; }
.tc-name {
  font-size: 11px; color: oklch(84% 0.1 70); font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin-bottom: 2px;
}
.tc-args {
  margin: 0; padding: 6px 9px; border-radius: 6px; background: oklch(25% 0.015 65);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10.5px; color: oklch(80% 0.02 70); white-space: pre-wrap;
  word-break: break-word; max-height: 130px; overflow: hidden;
}

/* ---- 分页签 ---- */
/* Request 与 Response 分签：长 prompt 展开后不再把 response 推出视口 */
.d-tabs {
  display: flex; gap: 2px; padding: 6px 13px 0;
  border-bottom: 1px solid oklch(30% 0.015 65);
}
.d-tab {
  padding: 6px 12px 7px; border: none; background: none; cursor: pointer;
  font-family: inherit; font-size: 11.5px; color: oklch(62% 0.02 70);
  border-bottom: 2px solid transparent; margin-bottom: -1px;
  display: inline-flex; align-items: center; gap: 5px;
}
.d-tab:hover { color: oklch(84% 0.03 75); }
.d-tab.on {
  color: oklch(93% 0.01 85); font-weight: 600;
  border-bottom-color: oklch(70% 0.12 60);
}
.d-tab-n {
  font-size: 9.5px; padding: 0 5px; border-radius: 8px;
  background: oklch(30% 0.015 65); color: oklch(74% 0.02 75);
  font-variant-numeric: tabular-nums;
}
.d-tab.on .d-tab-n { background: oklch(36% 0.03 55); color: oklch(88% 0.04 70); }

/* Request 签的工具行：条数 + 批量展开开关 */
.req-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 7px 13px 2px;
}
.req-note { font-size: 10px; color: oklch(60% 0.02 70); }
.req-all {
  background: none; border: none; padding: 0; cursor: pointer;
  font-family: inherit; font-size: 10.5px;
  color: oklch(70% 0.09 60); text-decoration: underline;
}
.req-all:hover { color: oklch(82% 0.1 65); }

/* ---- 工具入参的结构化渲染 ---- */
.ta { padding: 7px 13px 2px; }
.ta-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 3px; }
.ta-head .kf-k { min-width: 0; }
.ta-flag {
  font-size: 9.5px; padding: 1px 6px; border-radius: 4px;
  background: oklch(30% 0.04 80); color: oklch(78% 0.08 80);
  white-space: nowrap;
}
.ta-short {
  font-size: 11.5px; color: oklch(90% 0.01 85); line-height: 1.6;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  word-break: break-word;
}

.col-body::-webkit-scrollbar { width: 8px; }
.col-body::-webkit-scrollbar-thumb { background: oklch(86% 0.02 75); border-radius: 4px; }
.dark-body::-webkit-scrollbar-thumb { background: oklch(38% 0.015 65); }
</style>
