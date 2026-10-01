<template>
  <AdminShell :title="pageTitle" :subtitle="pageSubtitle">
    <template #actions>
      <!-- 状态与修复结论直接顶在标题右侧：这两个问题的答案决定要不要继续往下看 -->
      <span v-for="c in headChips" :key="c.text" class="head-tag" :class="c.tone">
        {{ c.text }}
      </span>
      <!-- trace_id 贯穿整个 run，是跨表/跨日志把一次执行串起来的唯一线索 -->
      <button v-if="traceId" class="back-btn" :title="traceId"
              @click="copy(traceId, 'trace')">
        {{ copyLabel('trace', '复制 trace_id') }}
      </button>
      <button class="back-btn" @click="router.push('/admin/traces')">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="m15 6-6 6 6 6" />
        </svg>
        返回列表
      </button>
    </template>

    <div v-if="errorMsg" class="error-bar">{{ errorMsg }}</div>

    <!-- ===== 需求汇总：整个需求的总量，不随轮次切换而变 =====
         轮次切换器管的是「看哪一轮」，这一行管的是「总共多少」——两者放一起会
         被误读成「当前轮次的量」，所以标题写清是需求级。 -->
    <div v-if="summary" class="sum-wrap">
      <div class="sum-head">
        <span class="sum-title">需求汇总</span>
        <span class="sum-note">整个需求，不随下方轮次切换而变</span>
      </div>
      <div class="sum-row">
        <div class="sum-card">
          <span class="sum-label">总耗时</span>
          <span class="sum-value">{{ fmtDuration(summary.duration_ms) }}</span>
          <!-- 首调用 + 长尾 = 总耗时。两个数字相加对得上，才说明取的是同一套口径。
               另外这里如实叫「首调用」：我们没有 TTFT 埋点，拿不到真正的首 Token。 -->
          <span class="sum-sub">
            首调用 {{ fmtDuration(summary.first_llm_ms) }} · 长尾 {{ fmtDuration(summary.tail_ms) }}
          </span>
        </div>

        <div class="sum-card">
          <span class="sum-label">LLM 调用</span>
          <span class="sum-value">{{ summary.llm_calls }}</span>
          <span class="sum-sub">{{ stageSummary }}</span>
        </div>

        <div class="sum-card">
          <span class="sum-label">总 Token</span>
          <span class="sum-value">{{ summary.tokens > 0 ? fmt(summary.tokens) : '—' }}</span>
          <span class="sum-sub">
            <template v-if="summary.tokens > 0">
              in {{ fmt(summary.tokens_in) }} · out {{ fmt(summary.tokens_out) }}
            </template>
            <template v-else>仅部分调用记录 usage</template>
          </span>
          <!-- 缓存命中单独一行：它是 in 的**子集**，塞进上一行会被读成第三类 token。
               分母只含上报过缓存信息的调用，样本量一并给出（见首页同款注释）。 -->
          <span v-if="summary.cache_hit_rate != null" class="sum-sub">
            缓存命中 {{ fmt(summary.cached_tokens) }}
            · {{ (summary.cache_hit_rate * 100).toFixed(1) }}% of 输入
            · {{ summary.cache_reported_calls }} 次上报
          </span>
        </div>

        <div class="sum-card">
          <span class="sum-label">总成本</span>
          <span class="sum-value">{{ summary.cost > 0 ? `¥${summary.cost.toFixed(4)}` : '—' }}</span>
          <span class="sum-sub">{{ modelSummary }}</span>
        </div>

        <!-- 重试 / 修复：判定序列直接取 verify 里程碑的 verdict，不靠猜 -->
        <div class="sum-card" :class="{ alert: repairAlert }">
          <span class="sum-label">重试 / 修复</span>
          <span class="sum-value" :class="{ bad: repairAlert }">{{ repairRatio }}</span>
          <span class="sum-sub">{{ verdictText }}</span>
        </div>

        <!-- 失败事件单独占一卡：它决定要不要继续往下查，不该藏在一行小字里 -->
        <div class="sum-card" :class="{ alert: summary.error_count > 0 }">
          <span class="sum-label">失败事件</span>
          <span class="sum-value" :class="{ bad: summary.error_count > 0 }">{{ summary.error_count }}</span>
          <span class="sum-sub">{{ summary.error_count > 0 ? '时间线上已标红' : '无' }}</span>
        </div>
      </div>

      <!-- ===== 阶段时间线 =====
           按阶段聚合成横条，回答「每个阶段总共花了多久」。与左栏时间线不重复：
           那边按连续段分组，回答的是「先后发生了什么」；这里给的是总览与占比。 -->
      <div v-if="stageSegments.length" class="stage-line">
        <div class="sl-head">
          <span class="sl-title">阶段时间线</span>
          <span class="sl-range">
            {{ fmtClock(summary.started_at) }} → {{ fmtClock(summary.last_event_at) }}
          </span>
          <!-- 每段是「该阶段首末事件之差」，会互相重叠（修复后编码器重入，
               编码与验收的时间窗是叠着的），相加并不等于总耗时。不写清楚的话，
               这条按比例拼满的横条会被读成「各阶段加起来正好等于全程」。 -->
          <span v-if="stagesOverlap" class="sl-note">跨度可重叠 · 条长仅示意相对长短</span>
        </div>
        <div class="sl-bar">
          <div v-for="s in stageSegments" :key="s.stage" class="sl-seg"
               :style="{ flexGrow: s.weight, background: s.color }"
               :title="`${s.label}：${s.events} 个事件 · 跨度 ${fmtDuration(s.ms)}（${fmtClock(s.started_at)} → ${fmtClock(s.ended_at)}）`">
            <span class="sl-name">{{ s.label }}</span>
            <span class="sl-meta">
              <template v-if="s.llm_calls">{{ s.llm_calls }} calls · </template>{{ fmtDuration(s.ms) }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== 轮次切换器：一行放不下就换行，不做横向滚动 ===== -->
    <div class="turn-switcher">
      <div class="switcher-head">
        <span class="switcher-title">对话轮次</span>
        <span class="switcher-summary">
          累计 {{ turns.length }} 轮 · {{ events.length }} 个事件
        </span>
      </div>
      <div class="pill-group">
        <button
          v-for="t in turns" :key="t.turn_index"
          class="pill" :class="{ active: activeTurn === t.turn_index }"
          @click="pickTurn(t.turn_index)">
          <span class="pill-num">{{ t.turn_index + 1 }}</span>
          <span class="pill-body">
            <span class="pill-mode">{{ t.mode }}</span>
            <span class="pill-meta">
              {{ fmtTime(t.started_at) }}→{{ fmtTime(t.ended_at) }} ·
              {{ fmtDuration(t.duration_ms) }} · {{ t.llm_calls }} 次调用
            </span>
          </span>
        </button>
        <span v-if="!turns.length && !loading" class="no-turn">暂无轮次数据</span>
      </div>
    </div>

    <!-- ===== 两栏：时间线（内联分组） + 事件详情 ===== -->
    <!-- 为什么不再单开一栏「执行树」：分组与排序不是两种视图，而是同一条时间线的
         两个属性。单开一栏只能是同一份数据的第二次渲染（同源、同筛选、同点击），
         而那一栏还叫「树」——数据里并没有父子关系字段，兑现不了层级承诺。 -->
    <div class="cols" ref="colsRef">
      <!-- 左：事件时间线（阶段 → 迭代 两级内联分组，可折叠） -->
      <section class="col" :style="{ width: leftW + 'px' }">
        <div class="col-head">
          <span class="col-title">事件时间线</span>
          <span class="col-count">{{ filtered.length }}</span>
        </div>
        <div class="filter-chips">
          <button class="fchip" :class="{ on: !kindFilter }" @click="kindFilter = ''">全部</button>
          <button
            v-for="f in filterChips" :key="f"
            class="fchip" :class="{ on: kindFilter === f }"
            @click="kindFilter = f">{{ kindLabel(f) }}</button>
        </div>
        <div class="col-body">
          <template v-for="row in rows" :key="row.key">
            <!-- 阶段分组头 -->
            <button v-if="row.t === 'stage'" class="grp-head grp-stage"
                    @click="toggleGroup(row.key)">
              <svg class="grp-caret" :class="{ open: isOpen(row.key) }"
                   viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
                   stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                <path d="m9 6 6 6-6 6" />
              </svg>
              <span class="grp-bar" :style="{ background: row.color }"></span>
              <span class="grp-title">{{ row.label }}</span>
              <span class="grp-meta">{{ row.count }} 步 · {{ fmtDuration(row.ms) }}</span>
            </button>

            <!-- 迭代分组头（仅编码阶段会出现：其余阶段的事件不带迭代号） -->
            <button v-else-if="row.t === 'iter'" class="grp-head grp-iter"
                    @click="toggleGroup(row.key)">
              <svg class="grp-caret" :class="{ open: isOpen(row.key) }"
                   viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
                   stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                <path d="m9 6 6 6-6 6" />
              </svg>
              <span class="grp-title">{{ row.label }}</span>
              <span class="grp-meta">{{ row.count }} 步 · {{ fmtDuration(row.ms) }}</span>
            </button>

            <!-- 事件条目：所属阶段/迭代已由分组头承担，副标题不再重复 -->
            <button v-else
                    class="evt" :class="[`d${row.depth}`, { 'evt-on': selectedId === row.e.id }]"
                    @click="openEvent(row.e.id)">
              <span class="evt-dot" :style="{ background: kindColor(row.e.kind) }"></span>
              <span class="evt-body">
                <span class="evt-time">{{ fmtClock(row.e.ts) }}</span>
                <span class="evt-label">{{ row.e.label || row.e.kind }}</span>
                <span class="evt-sub">
                  <template v-if="row.e.model">{{ row.e.model }} · </template>
                  <template v-if="row.e.duration_ms">{{ fmtDuration(row.e.duration_ms) }}</template>
                </span>
              </span>
              <span v-if="row.e.status === 'error'" class="evt-err">失败</span>
            </button>
          </template>
          <div v-if="!filtered.length && !loading" class="col-empty">没有匹配的事件</div>
        </div>
      </section>

      <!-- 拖拽手柄 -->
      <div class="handle" :class="{ dragging: dragging === 1 }"
           @mousedown="startDrag($event)">
        <span class="hd"></span><span class="hd"></span><span class="hd"></span>
      </div>

      <!-- 右：事件明细。宽度吃掉剩余空间 —— 中栏删掉后它是唯一需要大宽度的一栏 -->
      <section class="col col-detail">
        <div class="col-head">
          <span class="col-title">事件详情</span>
          <button v-if="detail" class="copy-btn" @click="copyJson">
            {{ copyLabel('json', '复制 JSON') }}
          </button>
        </div>
        <div class="col-body dark-body">
          <div v-if="detailLoading" class="dark-empty">加载中…</div>
          <div v-else-if="!detail" class="dark-empty">
            从左侧选择任意事件<br />查看完整输入与输出
          </div>
          <template v-else>
            <!-- 概览条 -->
            <div class="d-head">
              <div class="d-title">{{ detail.label || detail.kind }}</div>
              <div class="d-meta">
                {{ fmtClock(detail.ts) }} ·
                <template v-if="detail.duration_ms">{{ fmtDuration(detail.duration_ms) }} · </template>
                <template v-if="detail.model">{{ detail.model }}</template>
                <!-- call_id 是 llm_traffic.log 的索引键：点一下复制，就能直接
                     去传输层捞这次请求的原始报文。只显示前 8 位防换行，
                     title 与复制内容都是完整的。 -->
                <span v-if="detail.call_id" class="cid"
                      :title="`点击复制 call_id（完整：${detail.call_id}），可在 llm_traffic.log 里定位这次请求的原始报文`"
                      @click="copy(detail.call_id, 'cid')">· call_id {{ detail.call_id.slice(0, 8) }}<template v-if="copyState['cid'] === 'ok'"> 已复制</template></span>
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

            <!-- 结论要点：里程碑事件写入的 meta（判定 / 得分 / 未达成 AC / 修复轮次…）。
                 不渲染它，这些字段就只躺在库里 —— 界面上「验收为什么没过」没有答案，
                 排查还得回去翻 DB，等于埋点白做。 -->
            <template v-if="metaRows.length">
              <div class="d-section">要点</div>
              <div class="kf">
                <div v-for="row in metaRows" :key="row.k" class="kf-row">
                  <span class="kf-k" :title="row.k">{{ row.label }}</span>
                  <span class="kf-v" :class="{ alert: row.alert }">{{ row.v }}</span>
                </div>
              </div>
            </template>

            <!-- REQUEST -->
            <template v-if="hasArgs">
              <div class="d-section">ARGUMENTS</div>
              <div class="msg">
                <pre class="msg-body">{{ argsJson }}</pre>
              </div>
            </template>
            <div v-if="detail.messages.length" class="d-section">REQUEST</div>
            <div v-for="m in pagedMessages" :key="m.index" class="msg">
              <div class="msg-bar">
                <span class="role-badge" :style="{ background: roleColor(m.role) }">{{ m.role }}</span>
                <span v-if="m.name" class="msg-tag">{{ m.name }}</span>
                <span v-if="m.missing" class="msg-missing">正文缺失</span>
                <span v-else class="msg-len">{{ fmt(m.char_len) }} chars</span>
                <button v-if="!m.missing" class="msg-copy" @click.stop="copy(m.content ?? '', 'msg' + m.index)">{{ copyLabel('msg' + m.index, '复制') }}</button>
              </div>
              <pre v-if="m.missing" class="msg-body msg-gap">
这条 message 的正文没有存下来（blob 缺失），不是它本来为空。</pre>
              <pre v-else class="msg-body" :class="{ clipped: !expanded.has('m' + m.index) }">{{ m.content }}</pre>
              <button v-if="!m.missing && m.char_len > 700" class="more-btn" @click.stop="toggleMsg(m.index)">
                {{ expanded.has('m' + m.index) ? '收起' : `展开全部 ${fmt(m.char_len)} 字符` }}
              </button>
            </div>
            <button v-if="detail.messages.length > MSG_PAGE" class="more-btn"
                    @click="msgLimit = msgLimit > MSG_PAGE ? MSG_PAGE : 999">
              {{ msgLimit > MSG_PAGE ? '收起' : `显示其余 ${detail.messages.length - MSG_PAGE} 条` }}
            </button>

            <div v-if="detail.tools" class="msg">
              <div class="msg-bar">
                <span class="role-badge tools-b">tools</span>
                <span class="msg-len">{{ detail.tools.length }} 项工具定义</span>
              </div>
              <button class="more-btn" @click.stop="toggleMsg(-1)">
                {{ expanded.has('m-1') ? '收起' : '查看工具定义' }}
              </button>
              <pre v-if="expanded.has('m-1')" class="msg-body clipped">{{ toolsJson }}</pre>
            </div>

            <!-- RESPONSE -->
            <template v-if="detail.response">
              <div class="d-section">RESPONSE</div>
              <div v-if="detail.response.content" class="msg">
                <div class="msg-bar">
                  <span class="role-badge assistant-b">assistant</span>
                  <span class="msg-len">{{ fmt(String(detail.response.content).length) }} chars</span>
                  <button class="msg-copy" @click.stop="copy(detail.response.content, 'resp')">{{ copyLabel('resp', '复制') }}</button>
                </div>
                <pre class="msg-body" :class="{ clipped: !expanded.has('resp') }">{{ detail.response.content }}</pre>
                <button v-if="String(detail.response.content).length > 700"
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
              <pre class="msg-body clipped">{{ detail.tool_content }}</pre>
              <button class="more-btn" @click.stop="toggleMsg('tc')">
                {{ expanded.has('tc') ? '收起' : `展开全部 ${fmt(detail.tool_content.length)} 字符` }}
              </button>
            </div>
          </template>
        </div>
      </section>
    </div>
  </AdminShell>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AdminShell from '@/components/admin/AdminShell.vue'
import { adminFetch } from '@/composables/useAdmin'
import { buildTimelineRows } from './timelineRows'
// 状态的中文名与语义档位与首页列表共用一份（见 ./statusMeta）
import { statusLabel, statusTone } from './statusMeta'

interface Turn { turn_index: number; mode: string; event_count: number; llm_calls: number; started_at: string | null; ended_at: string | null; duration_ms: number }
// 阶段时间线的一段 —— 按阶段聚合（不是按连续段），一段回答「这个阶段共花了多久」
interface StageSeg {
  stage: string; events: number; llm_calls: number; ms: number
  started_at: string | null; ended_at: string | null
}
// 需求级汇总 —— 轮次可以单选切换，但「这个需求总共花了多少」必须一眼可见，
// 否则得自己把各轮加起来。耗时取墙钟跨度（首末事件时间差），不是各事件耗时之和。
interface Summary {
  turns: number; events: number; llm_calls: number; tool_calls: number
  error_count: number; tokens: number; tokens_in: number; tokens_out: number
  /** cached 是 tokens_in 的子集，不是并列的第三类 token */
  cached_tokens: number
  /** null = 本需求没有任何调用上报缓存信息（≠ 0%，0% 是查过但没命中） */
  cache_hit_rate: number | null
  cache_reported_calls: number
  cost: number; duration_ms: number
  // 首调用 + 长尾 == duration_ms，由后端保证；前端只负责展示，不自己凑数
  first_llm_ms: number; tail_ms: number
  started_at: string | null; last_event_at: string | null
  stages: StageSeg[]
  by_model: { model: string; calls: number; cost: number }[]
  verify_verdicts: (string | null)[]
  repair_rounds: number
  passed: boolean
}
// 需求基本信息（随 /turns 一起下发，省掉「去列表里捞标题」那次请求）
interface ReqInfo {
  id: number; title: string; status: string
  creator: string; created_at: string | null
  trace_id: string | null
}
interface Ev {
  id: number; seq: number; turn_index: number; iteration: number | null
  ts: string | null; kind: string; stage: string | null; label: string | null
  status: string; model: string | null; duration_ms: number | null
  tokens_in: number; tokens_out: number; has_payload: boolean; message_count: number
}
// missing：正文没存下来（blob 缺失）。后端不再把它静默成空串 ——
// 空串分不清「这条本来就是空的」和「没存下来」，后者是事故。
interface Msg {
  index: number; role: string; content: string | null; char_len: number
  missing?: boolean; name?: string
}

// 各家 LLM 的 tool_call 形状不统一：OpenAI 系用 function.{name,arguments} 嵌套，
// 部分厂商是扁平的 name/arguments，两种都要能读。
interface ToolCall {
  function?: { name?: string; arguments?: string }
  name?: string
  arguments?: string
}
interface Detail {
  id: number; label: string | null; kind: string; ts: string | null
  model: string | null; duration_ms: number | null
  trace_id: string | null; call_id: string | null
  tokens_in: number; tokens_out: number
  messages: Msg[]; tools: unknown[] | null
  response: { content?: string; tool_calls?: ToolCall[] } | null
  tool_content: string | null
  // tool_call 事件的工具参数（LLM 事件里则是 messages/tools 之外的请求参数）
  request_params: Record<string, unknown> | null
  // 里程碑事件的结论字段（判定、得分、未达成 AC、修复轮次…）
  meta: Record<string, unknown> | null
}

const route = useRoute()
const router = useRouter()
const reqId = computed(() => route.params.id as string)

const turns = ref<Turn[]>([])
const summary = ref<Summary | null>(null)
const events = ref<Ev[]>([])
const detail = ref<Detail | null>(null)
const reqInfo = ref<ReqInfo | null>(null)
const loading = ref(false)
const detailLoading = ref(false)
const errorMsg = ref('')
const activeTurn = ref(0)
const kindFilter = ref('')
const selectedId = ref<number | null>(null)

const MSG_PAGE = 3
const msgLimit = ref(MSG_PAGE)
const expanded = ref<Set<string>>(new Set())
// 折叠状态 = 「用户显式点过的组」+「没点过时的默认值」。
// 不预填 key：分组的 key 由 rows 生成，页面这边猜不出来（曾想用 `s${i}` 预填，
// 但下标在换筛选后指向另一段，会张冠李戴）。
const openState = ref<Map<string, boolean>>(new Map())
const defaultOpen = ref(true)

// ---- 两栏宽度（可拖拽）----
const leftW = ref(260)
const dragging = ref(0)
const colsRef = ref<HTMLElement | null>(null)

// 事件类型的中文名与配色由后端契约下发（/api/admin/traces/contract）。
// 新增阶段只改后端 event_contract.py，前端自动跟上 —— 不需要再同步两张 map。
// 这张本地 map 仅是契约还没拉到时的首帧兜底。
const KIND_FALLBACK: Record<string, string> = {
  intent: '意图', memory: '记忆', clarify: '澄清', plan: '规划', coding: '编码',
  llm_turn: 'LLM', tool_call: '工具', verify: '验收', repair: '修复', deliver: '交付',
}
type KindSpec = { label: string | null; color: string; stage: string | null }
type Contract = {
  kinds: Record<string, KindSpec>
  stages: Record<string, { label: string | null; color: string }>
  filter_kinds: string[]
  fallback_kind: KindSpec
  fallback_stage: { label: string | null; color: string }
}
const contract = ref<Contract | null>(null)

const kindSpec = (k: string): KindSpec =>
  contract.value?.kinds[k]
  ?? contract.value?.fallback_kind
  ?? { label: null, color: 'oklch(60% 0.02 70)', stage: null }
const kindColor = (k: string) => kindSpec(k).color
// 契约里没有的类型显示原名 —— 不假装认识它，否则分不清是新阶段还是脏数据
const kindLabel = (k: string) => kindSpec(k).label ?? KIND_FALLBACK[k] ?? k
const stageLabel = (s: string | null) =>
  s ? (contract.value?.stages[s]?.label ?? s) : ''
const filterChips = computed(() =>
  contract.value?.filter_kinds ?? ['llm_turn', 'tool_call', 'memory'])

// ---- 头部：标题 / 副标题 / 状态与修复结论 ----
// trace_id 随需求信息一起下发 —— 注意 /events 接口**不带**这个字段，
// 别想从事件列表里推出来。它是把这次执行在 agent_events 与 llm_traffic.log
// 之间串起来的唯一线索，头部必须能一键复制到。
const traceId = computed(() => reqInfo.value?.trace_id ?? '')

// 复制按钮的瞬时状态：{ [按钮 key]: 'ok' | 'fail' }。复制是「按了没有任何
// 视觉变化」的动作，没有这个状态就分不清「复制成功」和「按钮没反应」。
const copyState = ref<Record<string, 'ok' | 'fail'>>({})

const pageTitle = computed(() => {
  const t = reqInfo.value?.title
  return t ? `需求轨迹 / #${reqId.value} ${t}` : `需求轨迹 / #${reqId.value}`
})

const pageSubtitle = computed(() => {
  const r = reqInfo.value
  const parts: string[] = []
  if (r?.creator) parts.push(`用户 ${r.creator}`)
  if (r?.created_at) parts.push(`提交于 ${fmtFull(r.created_at)}`)
  if (summary.value) parts.push(`总耗时 ${fmtDuration(summary.value.duration_ms)}`)
  if (traceId.value) parts.push(`trace_id: ${traceId.value.slice(0, 8)}…`)
  return parts.join(' · ')
})

// 头部 chip：状态的答案 + 「修了几轮才过」的答案。这两个问题决定了
// 要不要继续往下翻时间线。
const headChips = computed(() => {
  const out: { text: string; tone: string }[] = []
  const st = reqInfo.value?.status
  if (st) out.push({ text: statusLabel(st), tone: statusTone(st) })
  const s = summary.value
  if (s && s.verify_verdicts.length) {
    out.push(s.passed
      ? {
        text: s.repair_rounds > 0 ? `修复 ${s.repair_rounds} 轮后通过` : '一次通过',
        tone: 'ok',
      }
      : { text: '最终未通过', tone: 'bad' })
  }
  return out
})

// ---- 汇总卡的副指标 ----
// 分阶段调用数：设计稿写的是「TL 1 · Coder 17」，我们用事件自带的 stage
// 分组算，调用数为 0 的阶段不列出来（列了只会让人以为它没跑）
const stageSummary = computed(() => {
  const s = summary.value
  if (!s?.stages?.length) return `工具调用 ${s?.tool_calls ?? 0} 次`
  const parts = s.stages
    .filter(x => x.llm_calls > 0)
    .map(x => `${stageLabel(x.stage)} ${x.llm_calls}`)
  return parts.length ? parts.join(' · ') : '无 LLM 调用'
})

const modelSummary = computed(() => {
  const m = summary.value?.by_model ?? []
  if (!m.length) return '未记录模型'
  const head = m.slice(0, 3).map(x => `${x.model} ${x.calls}`)
  return m.length > 3 ? `${head.join(' · ')} 等 ${m.length} 个模型` : head.join(' · ')
})

// 「修复次数 / 验收轮数」。分母为 0（老数据没有 verify 里程碑）时显示 —，
// 而不是假装 0 —— 0 会被读成「一轮就过」。
const repairRatio = computed(() => {
  const s = summary.value
  if (!s) return '—'
  if (!s.verify_verdicts.length) return `${s.repair_rounds} / —`
  return `${s.repair_rounds} / ${s.verify_verdicts.length}`
})

// 逐轮判定：把 verify 里程碑的 verdict 按发生顺序念出来。超过 4 轮只给首尾 ——
// 一行副标题放不下，也没人会去读第 5 轮的细节。
const verdictText = computed(() => {
  const v = summary.value?.verify_verdicts ?? []
  const tag = (x: string | null, i: number) =>
    `第 ${i + 1} 轮 ${x ? String(x).toUpperCase() : '未知'}`
  if (!v.length) {
    const n = summary.value?.repair_rounds ?? 0
    return n > 0 ? `修复 ${n} 轮（无判定记录）` : '无验收判定记录'
  }
  if (v.length <= 4) return v.map(tag).join(' · ')
  return [tag(v[0], 0), '…', tag(v[v.length - 1], v.length - 1)].join(' · ')
})

// 只有「最终没通过」才标红。中间某轮 FAIL 是正常流程（后面修好了），
// 把它标红会让每一次多轮修复看起来都像事故。
const repairAlert = computed(() => {
  const s = summary.value
  if (!s) return false
  return s.verify_verdicts.length > 0 && !s.passed
})

// ---- 阶段时间线 ----
// 宽度按耗时占比分配；全部阶段耗时都是 0（事件只有时间点、没有 duration）
// 时退化成等宽，而不是挤成一条看不见的线。
const stageSegments = computed(() => {
  const segs = summary.value?.stages ?? []
  if (!segs.length) return []
  const total = segs.reduce((a, x) => a + Math.max(0, x.ms), 0)
  // 按起始时间排序 —— 叫「时间线」就得有先后。后端的 stages 是按阶段枚举
  // 出的，直接拿来会变成「编码 / 规划 / 修复 / 验收」这种没有依据的顺序。
  return [...segs]
    .sort((a, b) => String(a.started_at ?? '').localeCompare(String(b.started_at ?? '')))
    .map(x => ({
      ...x,
      label: stageLabel(x.stage),
      color: contract.value?.stages[x.stage]?.color ?? 'oklch(62% 0.05 70)',
      weight: total > 0 ? Math.max(1, x.ms) : 1,
    }))
})

// 各阶段跨度之和超过总耗时 → 时间窗确实互相重叠（实测某需求四段加起来是总耗时的
// 1.19 倍）。此时横条只能示意相对长短，绝不能让人以为它在按时间轴切分全程。
const stagesOverlap = computed(() => {
  const s = summary.value
  if (!s?.stages?.length) return false
  const sum = s.stages.reduce((a, x) => a + Math.max(0, x.ms), 0)
  return sum > s.duration_ms * 1.02 + 1000
})

const ROLE_COLOR: Record<string, string> = {
  system: 'oklch(48% 0.14 250)',
  user: 'oklch(50% 0.13 150)',
  assistant: 'oklch(48% 0.15 300)',
  tool: 'oklch(58% 0.13 70)',
}
const roleColor = (r: string) => ROLE_COLOR[r] ?? 'oklch(55% 0.02 70)'

const filtered = computed(() =>
  kindFilter.value ? events.value.filter(e => e.kind === kindFilter.value) : events.value)

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
  blocked: '已拦截', unmet_acs: '未达成 AC', repair_rounds: '修复轮数',
  gate: '闸门', restored_files: '已恢复文件', removed_files: '已删除文件',
  summary: '摘要', injected: '注入条数', hit_ids: '命中记忆', hit: '命中条数',
  error: '错误', message_count: '消息数', content_ref: '内容指纹',
  thinking: '含思考', has_usage: '含用量', source: '来源',
}
const ALERT_KEYS = new Set(['critical_count', 'failed_ac_ids', 'defect_count',
  'target_defects', 'error', 'dod_issues'])
// 过程性字段的「默认值」：等于默认值就不显示。全渲染会让「要点」退化成噪声
// （每个 tool_call 都顶一行「已拦截 否」），排查时反而看不见真正的结论。
// 反过来说，blocked=true、thinking=false 这类**偏离默认**的值会照常出现。
const NEUTRAL_META: Record<string, unknown> = {
  blocked: false, thinking: false, has_usage: true,
}
// 纯内部标识，对排查没有信息量（正文本身就在下面）
const HIDDEN_META = new Set(['content_ref', 'message_count'])

const fmtMetaValue = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (Array.isArray(v)) {
    if (!v.length) return '无'
    const head = v.slice(0, 6)
      .map(x => (typeof x === 'object' && x !== null ? JSON.stringify(x) : String(x)))
    return v.length > 6 ? `${head.join('、')} 等 ${v.length} 项` : head.join('、')
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
  const m = detail.value?.meta
  if (!m || typeof m !== 'object') return []
  return Object.entries(m)
    .filter(([k, v]) => !HIDDEN_META.has(k)
      && !(k in NEUTRAL_META && v === NEUTRAL_META[k]))
    .map(([k, v]) => ({
      k, label: META_LABEL[k] ?? k, v: fmtMetaValue(v), alert: metaAlert(k, v),
    }))
})

// ---- 时间线的两级分组 ----
// 分组与排序不是两种视图，而是同一条时间线的两个属性 —— 所以这里不再单开一栏，
// 层级直接内联在时间线里。规则（阶段连续段 → 编码迭代）抽在 ./timelineRows：
// 纯函数，可以拿真实事件逐条断言，而不是靠肉眼看截图判断分组对不对。
//
// 事件多到一定程度时默认折叠各阶段，首屏先给一张「分了几段、每段几步」的目录。
// 折叠能力是合并成一栏之后必须保住的 —— 否则长链路反而比改版前更难读。
const AUTO_COLLAPSE_OVER = 40

const rows = computed(() => buildTimelineRows<Ev>(filtered.value, {
  isOpen,
  label: stageLabel,
  color: s => contract.value?.stages[s]?.color ?? 'oklch(60% 0.02 70)',
}))

const pagedMessages = computed(() => detail.value?.messages.slice(0, msgLimit.value) ?? [])
const toolsJson = computed(() => JSON.stringify(detail.value?.tools ?? [], null, 2))
const argsJson = computed(() => {
  const p = detail.value?.request_params
  return p ? JSON.stringify(p, null, 2) : ''
})
const hasArgs = computed(() => {
  const p = detail.value?.request_params
  return !!p && Object.keys(p).length > 0
})

// ---- 格式化 ----
function pad(n: number) { return String(n).padStart(2, '0') }
function fmtDuration(ms: number | null): string {
  if (!ms) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`
}
function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
function fmtClock(iso: string | null): string {
  if (!iso) return '--:--:--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--:--:--'
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
// 完整时间（带秒）：「提交于 2026-09-30 13:12:04」要精确到秒 ——
// 同一天提交的多个需求，只到分钟分不出谁先谁后。
function fmtFull(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
    + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
function fmt(n: number): string {
  // 与列表页同款分档：token 量级跨好几个数量级，单位写死任一个都不好读
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  return n >= 10000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

// ---- 数据 ----
async function loadTurns() {
  const d = await adminFetch<{
    items: Turn[]; summary?: Summary; requirement?: ReqInfo
  }>(`/api/admin/traces/${reqId.value}/turns`)
  turns.value = d.items
  // summary 是后端算的需求级总量。这里不拿当前轮次的 events 自己加 ——
  // 详情页每次只加载**一个轮次**的事件，前端求和只会得到那一轮的，不是整个需求。
  summary.value = d.summary ?? null
  // 需求基本信息随同一次请求下发：以前是去列表接口里翻标题，需求翻不到
  // 那一页时页面标题就空了，而「翻不到」恰恰是最需要知道标题的时候。
  reqInfo.value = d.requirement ?? null
  const first = d.items[0]?.turn_index ?? 0
  activeTurn.value = first
}

async function loadEvents() {
  const d = await adminFetch<{ items: Ev[] }>(
    `/api/admin/traces/${reqId.value}/events?turn_index=${activeTurn.value}&limit=1000`)
  events.value = d.items
  // 换轮次 / 换需求时重置折叠选择，否则上一轮的展开状态会带到下一轮
  openState.value = new Map()
  // 长链路首屏折叠成目录，短链路全展开 —— 改版不该把原本一眼可见的东西藏起来
  defaultOpen.value = d.items.length <= AUTO_COLLAPSE_OVER
}

async function loadContract() {
  try {
    contract.value = await adminFetch<Contract>('/api/admin/traces/contract')
  } catch { /* 拉不到就走本地兜底，不阻断页面 */ }
}

async function openEvent(id: number) {
  if (selectedId.value === id && detail.value) return
  selectedId.value = id
  detailLoading.value = true
  expanded.value = new Set()
  msgLimit.value = MSG_PAGE
  try {
    detail.value = await adminFetch<Detail>(`/api/admin/traces/events/${id}`)
  } catch (e) {
    errorMsg.value = (e as Error).message || '加载事件失败'
  } finally {
    detailLoading.value = false
  }
}

function pickTurn(t: number) {
  activeTurn.value = t
  selectedId.value = null
  detail.value = null
  loadEvents()
}

// 没点过的组跟着 defaultOpen 走（长链路首屏折叠，见 AUTO_COLLAPSE_OVER）；
// 点过就以用户的显式选择为准，且该选择会被记住。
const isOpen = (k: string) => openState.value.get(k) ?? defaultOpen.value

function toggleGroup(k: string) {
  const m = new Map(openState.value)
  m.set(k, !isOpen(k))
  openState.value = m
}

function toggleMsg(k: string | number) {
  const key = String(k).startsWith('m') || typeof k === 'number' ? `m${k}` : String(k)
  const s = new Set(expanded.value)
  s.has(key) ? s.delete(key) : s.add(key)
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
  if (detail.value) await copy(JSON.stringify(detail.value, null, 2), 'json')
}

// ---- 拖拽：两栏之间只有一根分隔条，调的是时间线宽度 ----
function startDrag(ev: MouseEvent) {
  dragging.value = 1
  ev.preventDefault()
  const startX = ev.clientX
  const startLeft = leftW.value
  const box = colsRef.value?.getBoundingClientRect()
  const total = box?.width ?? 1400

  const onMove = (e: MouseEvent) => {
    // 上限留出详情栏的空间 —— 时间线再宽也不该把报文挤掉
    leftW.value = Math.min(total * 0.6, Math.max(190, startLeft + e.clientX - startX))
  }
  const onUp = () => {
    dragging.value = 0
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
  }
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'col-resize'
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

onMounted(async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    await Promise.all([loadContract(), loadTurns(), loadEvents()])
  } catch (e) {
    errorMsg.value = (e as Error).message || '加载失败'
  } finally {
    loading.value = false
  }
  // 默认选中首个 LLM 调用，避免右栏长期空着
  const firstLlm = events.value.find(e => e.kind === 'llm_turn')
  if (firstLlm) openEvent(firstLlm.id)
})

onBeforeUnmount(() => { dragging.value = 0 })
watch(() => route.params.id, () => { selectedId.value = null; detail.value = null })
</script>

<style scoped>
.back-btn {
  display: inline-flex; align-items: center; gap: 5px; padding: 6px 12px;
  border: 1px solid oklch(88% 0.02 75); border-radius: 8px; background: #fff;
  font-size: 12.5px; cursor: pointer; font-family: inherit; color: oklch(32% 0.02 60);
}
.error-bar {
  background: oklch(95% 0.05 25); border: 1px solid oklch(88% 0.08 25);
  color: oklch(45% 0.15 25); padding: 10px 14px; border-radius: 9px;
  margin-bottom: 12px; font-size: 13px;
}

/* ---- 头部 chip：状态 + 修复结论 ---- */
.head-tag {
  font-size: 11.5px; font-weight: 600; padding: 3px 10px; border-radius: 999px;
  white-space: nowrap;
}
.head-tag.ok { background: var(--color-success-soft); color: var(--color-success); }
.head-tag.warn { background: var(--color-warning-soft); color: var(--color-warning); }
.head-tag.bad { background: var(--color-danger-soft); color: var(--color-danger); }
.head-tag.info { background: color-mix(in oklab, var(--color-info) 14%, transparent); color: var(--color-info); }
.head-tag.muted { background: var(--accent-soft); color: var(--muted); }

/* ---- 阶段时间线 ---- */
/* 与左栏时间线不重复：那边是「先后发生了什么」，这里是「各阶段各占多久」 */
.stage-line { margin-top: 12px; }
.sl-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 10px; margin-bottom: 6px;
}
.sl-title { font-size: 12.5px; font-weight: 600; color: var(--fg); }
.sl-range {
  font-size: 11px; color: var(--faint);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
/* 重叠提示：不加这句，按比例拼满的横条会被读成「各阶段加起来等于全程」 */
.sl-note { font-size: 10.5px; color: var(--faint); margin-left: auto; }
.sl-bar {
  display: flex; gap: 3px; height: 46px; border-radius: 10px; overflow: hidden;
}
/* 宽度按耗时占比（flex-grow + basis 0），并给最小宽度兜底：只有一条里程碑
   事件的阶段耗时接近 0，不设下限就会缩成一条看不见的细线 */
.sl-seg {
  flex-basis: 0; min-width: 58px; padding: 7px 9px; overflow: hidden;
  display: flex; flex-direction: column; justify-content: center; gap: 2px;
  border-radius: 8px; color: #fff;
}
.sl-name {
  font-size: 11.5px; font-weight: 600; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.sl-meta {
  font-size: 10px; opacity: .85; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; font-variant-numeric: tabular-nums;
}

/* ---- 需求汇总指标 ---- */
.sum-wrap { margin-bottom: 14px; }
.sum-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px; }
.sum-title { font-size: 12.5px; font-weight: 600; color: var(--fg); }
.sum-note { font-size: 11px; color: var(--faint); }
.sum-row {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
  gap: 10px;
}
.sum-card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 11px;
  padding: 11px 13px; display: flex; flex-direction: column; gap: 3px;
}
.sum-card.alert { border-color: color-mix(in oklab, var(--color-danger) 45%, var(--border)); }
.sum-label { font-size: 11.5px; color: var(--muted); }
.sum-value {
  font-size: 19px; font-weight: 600; color: var(--fg); line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.sum-value.bad { color: var(--color-danger); }
.sum-sub { font-size: 10.5px; color: var(--faint); line-height: 1.45; }

/* ---- 轮次切换器 ---- */
.turn-switcher {
  background: #fff; border: 1px solid oklch(90% 0.02 75); border-radius: 12px;
  padding: 13px 16px; margin-bottom: 14px;
}
.switcher-head {
  display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px;
}
.switcher-title { font-size: 12.5px; font-weight: 600; color: oklch(38% 0.02 60); }
.switcher-summary { font-size: 11.5px; color: oklch(60% 0.02 70); }
.pill-group { display: flex; flex-wrap: wrap; gap: 7px; }
.pill {
  display: inline-flex; align-items: center; gap: 9px; padding: 7px 13px;
  border: 1px solid oklch(89% 0.02 75); border-radius: 9px; background: #fff;
  cursor: pointer; font-family: inherit; text-align: left;
  transition: border-color .14s, background .14s;
}
.pill:hover { border-color: oklch(75% 0.06 40); }
.pill.active {
  background: oklch(66% 0.15 32); border-color: oklch(66% 0.15 32);
}
.pill-num {
  width: 21px; height: 21px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11.5px; font-weight: 700; flex-shrink: 0;
  background: oklch(94% 0.03 60); color: oklch(45% 0.02 60);
}
.pill.active .pill-num { background: oklch(100% 0 0 / .28); color: #fff; }
.pill-body { display: flex; flex-direction: column; gap: 1px; }
.pill-mode { font-size: 12.5px; font-weight: 600; color: oklch(30% 0.02 60); }
.pill-meta { font-size: 10.5px; color: oklch(58% 0.02 70); }
.pill.active .pill-mode { color: #fff; }
.pill.active .pill-meta { color: oklch(100% 0 0 / .78); }
.no-turn { font-size: 12.5px; color: oklch(62% 0.02 70); }

/* ---- 两栏 ---- */
/* 必须给固定高度：没有高度约束时 flex:1 + overflow-y:auto 不生效，
   内容会把整页撑开（50 个事件 → 页面 3000px 高），两栏也各自拉长 */
.cols {
  display: flex; align-items: stretch; gap: 0;
  height: calc(100vh - 330px); min-height: 520px;
}
.col {
  display: flex; flex-direction: column; min-width: 0;
  background: #fff; border: 1px solid oklch(90% 0.02 75); border-radius: 12px;
  overflow: hidden;
}
/* 时间线宽度由拖拽决定、不参与伸缩；详情栏吃掉剩余空间 */
.col:first-child { flex-shrink: 0; }
.col-detail { flex: 1; }
.col-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 11px 14px; border-bottom: 1px solid oklch(93% 0.02 75);
  background: oklch(98.5% 0.01 80); flex-shrink: 0;
}
.col-title { font-size: 12px; font-weight: 600; color: oklch(40% 0.02 60); }
.col-count {
  font-size: 10.5px; color: oklch(58% 0.02 70); background: oklch(94% 0.02 75);
  padding: 1px 7px; border-radius: 9px;
}
.col-body { flex: 1; overflow-y: auto; padding: 8px; }
.col-empty { padding: 34px 12px; text-align: center; color: oklch(70% 0.02 70); font-size: 12.5px; }

.filter-chips {
  display: flex; gap: 5px; padding: 8px 10px; flex-wrap: wrap;
  border-bottom: 1px solid oklch(94% 0.02 75);
}
.fchip {
  padding: 3px 9px; border-radius: 6px; border: 1px solid oklch(89% 0.02 75);
  background: #fff; font-size: 11px; cursor: pointer; font-family: inherit;
  color: oklch(48% 0.02 65);
}
.fchip.on { background: oklch(30% 0.02 60); color: #fff; border-color: oklch(30% 0.02 60); }

/* ---- 时间线条目 ---- */
.evt {
  display: flex; gap: 9px; width: 100%; text-align: left; padding: 7px 8px;
  border: none; background: none; border-radius: 8px; cursor: pointer;
  font-family: inherit; transition: background .12s;
}
.evt:hover { background: oklch(97.5% 0.01 80); }
.evt-on { background: oklch(95% 0.04 45); }
.evt-dot { width: 7px; height: 7px; border-radius: 50%; margin-top: 4px; flex-shrink: 0; }
.evt-body { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.evt-time {
  font-size: 10px; color: oklch(62% 0.02 70);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.evt-label { font-size: 12px; color: oklch(28% 0.02 60); font-weight: 500; }
.evt-sub { font-size: 10px; color: oklch(62% 0.02 70); }

/* ---- 时间线内的两级分组头（阶段 → 迭代） ---- */
.grp-head {
  display: flex; align-items: center; gap: 7px; width: 100%; text-align: left;
  padding: 6px 8px; border: none; background: none; border-radius: 7px;
  cursor: pointer; font-family: inherit; transition: background .12s;
}
.grp-head:hover { background: oklch(96% 0.015 80); }
/* 阶段是视觉主轴：底色 + 上间距，一眼看出时间线分成几段 */
.grp-stage { margin-top: 6px; background: oklch(97% 0.02 80); }
.grp-stage:first-child { margin-top: 0; }
/* 迭代是阶段内的次级轴：只缩进、不加底色，避免和阶段抢注意力 */
.grp-iter { padding-left: 24px; }
.grp-bar { width: 3px; height: 13px; border-radius: 2px; flex-shrink: 0; }
.grp-caret { transition: transform .15s; color: oklch(55% 0.02 70); flex-shrink: 0; }
.grp-caret.open { transform: rotate(90deg); }
.grp-title { font-size: 12px; font-weight: 600; color: oklch(32% 0.02 60); }
.grp-meta { font-size: 10.5px; color: oklch(62% 0.02 70); margin-left: auto; }
/* 事件按层级缩进：直接挂在阶段下的（如「编码收尾」）与迭代内的差一级 */
.evt.d1 { padding-left: 22px; }
.evt.d2 { padding-left: 36px; }
.evt-err {
  margin-left: auto; font-size: 10px; color: oklch(50% 0.18 25);
  background: oklch(94% 0.05 25); padding: 1px 6px; border-radius: 4px; flex-shrink: 0;
}

/* ---- 拖拽手柄 ---- */
.handle {
  width: 11px; flex-shrink: 0; cursor: col-resize;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 3px; border-radius: 4px; transition: background .14s;
}
.handle:hover, .handle.dragging { background: oklch(92% 0.04 45); }
.hd { width: 3px; height: 3px; border-radius: 50%; background: oklch(66% 0.02 70); }
.handle:hover .hd, .handle.dragging .hd { background: oklch(60% 0.14 32); }

/* ---- 右栏深色明细 ---- */
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
.col-body::-webkit-scrollbar { width: 8px; }
.col-body::-webkit-scrollbar-thumb { background: oklch(86% 0.02 75); border-radius: 4px; }
.dark-body::-webkit-scrollbar-thumb { background: oklch(38% 0.015 65); }
</style>
