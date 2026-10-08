<template>
  <section class="col" :style="width ? { width: width + 'px' } : undefined">
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
                @click="emit('select', row.e.id)">
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
</template>

<script setup lang="ts">
/**
 * 事件时间线（左栏）—— 需求轨迹页与评测页共用。
 *
 * 为什么抽成组件：两处渲染的必须是**同一条时间线**。分散在两个页面里各写一份，
 * 分组规则一改就会有一边漏改，而漏改在界面上只表现为「看着有点怪」。
 * 分组规则本身早已抽在 `views/admin/timelineRows`（纯函数，可拿真实事件断言）。
 *
 * 组件只管渲染与折叠，数据加载与筛选数据源留给调用方。
 */
import { computed, ref, watch } from 'vue'

import { buildTimelineRows, type TimelineRow } from '@/views/admin/timelineRows'
import { KIND_FALLBACK, type Contract, type Ev, type KindSpec } from '@/types/trace'
import { fmtClock, fmtDuration } from '@/utils/format'

const props = defineProps<{
  events: Ev[]
  contract: Contract | null
  selectedId: number | null
  loading?: boolean
  /** 左栏宽度（可拖拽），不传则由 CSS 决定 */
  width?: number
}>()

const emit = defineEmits<{ select: [id: number] }>()

// 首屏折叠阈值：事件多到一定程度时先给一张「分了几段、每段几步」的目录
const AUTO_COLLAPSE_OVER = 40

const kindFilter = ref('')
// 折叠状态 = 「用户显式点过的组」+「没点过时的默认值」。
// 不预填 key：分组的 key 由 rows 生成，组件这边猜不出来（曾想用 `s${i}` 预填，
// 但下标在换筛选后指向另一段，会张冠李戴）。
const openState = ref<Map<string, boolean>>(new Map())
const defaultOpen = ref(true)

// 换数据（换轮次 / 换题）时重置折叠选择，否则上一轮的展开状态会带到下一轮
watch(() => props.events, list => {
  openState.value = new Map()
  defaultOpen.value = list.length <= AUTO_COLLAPSE_OVER
})

const kindSpec = (k: string): KindSpec =>
  props.contract?.kinds[k]
  ?? props.contract?.fallback_kind
  ?? { label: null, color: 'oklch(60% 0.02 70)', stage: null }
const kindColor = (k: string) => kindSpec(k).color
// 契约里没有的类型显示原名 —— 不假装认识它，否则分不清是新阶段还是脏数据
const kindLabel = (k: string) => kindSpec(k).label ?? KIND_FALLBACK[k] ?? k
const stageLabel = (s: string | null) =>
  s ? (props.contract?.stages[s]?.label ?? s) : ''
const filterChips = computed(() =>
  props.contract?.filter_kinds ?? ['llm_turn', 'tool_call', 'memory'])

const filtered = computed(() =>
  kindFilter.value ? props.events.filter(e => e.kind === kindFilter.value) : props.events)

// 没点过的组跟着 defaultOpen 走（长链路首屏折叠）；点过就以用户的显式选择为准
const isOpen = (k: string) => openState.value.get(k) ?? defaultOpen.value

function toggleGroup(k: string) {
  const m = new Map(openState.value)
  m.set(k, !isOpen(k))
  openState.value = m
}

const rows = computed<TimelineRow<Ev>[]>(() => buildTimelineRows<Ev>(filtered.value, {
  isOpen,
  label: stageLabel,
  color: s => props.contract?.stages[s]?.color ?? 'oklch(60% 0.02 70)',
}))
</script>

<style scoped>
.col {
  display: flex; flex-direction: column; min-width: 0;
  background: #fff; border: 1px solid oklch(90% 0.02 75); border-radius: 12px;
  overflow: hidden;
}
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

/* ---- 两级分组头（阶段 → 迭代） ---- */
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

.col-body::-webkit-scrollbar { width: 8px; }
.col-body::-webkit-scrollbar-thumb { background: oklch(86% 0.02 75); border-radius: 4px; }
</style>
