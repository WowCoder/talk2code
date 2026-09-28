<template>
  <div class="dialogue-panel">
    <div class="dialogue-header">AI 对话</div>
    <div class="dialogue-body" ref="bodyRef">
      <template v-for="(msg, i) in messages" :key="i">
        <!-- wrapper 用 display:contents，不生成盒模型，因此不破坏 .msg 的
             flex align-self 左右对齐；时间条作为独立 flex item 统一渲染 -->
        <div class="msg-wrapper">
          <DialogueMessage :msg="msg" />
          <div v-if="msgTime(msg)" class="msg-time">{{ msgTime(msg) }}</div>
        </div>
      </template>

      <!-- Plan 确认卡片（TL 完成后展示） -->
      <PlanConfirmation
        v-if="showPlanConfirmation"
        :spec-data="specData"
      />

      <!-- Dynamic components from SSE（仅未提交的表单浮动展示；已提交的以消息形式在上方消息流中渲染） -->
      <QuestionForm
        v-if="questionForm && !questionForm.submitted && !showPlanConfirmation"
        :form-data="questionForm"
        :mode="questionFormMode"
        @submitted="onQuestionSubmitted"
      />
      <ExecutionPanel :trace-data="traceSummary" />

      <!-- 执行进度：显示后台"当前在做什么" + 阶段 + 静默期等待时长。
           此前这里是一句硬编码的"AI 正在处理…"，不绑定任何后端状态，
           用户在长达数分钟的编码/验证期里得不到任何信息。 -->
      <div v-if="isLoading" class="progress-card">
        <div class="pc-head">
          <div class="lb-dots">
            <span class="lb-dot"></span>
            <span class="lb-dot"></span>
            <span class="lb-dot"></span>
          </div>
          <span class="pc-action">{{ activityText }}</span>
        </div>
        <div class="pc-stages">
          <span
            v-for="(s, i) in STAGES"
            :key="s.key"
            class="pc-stage"
            :class="{
              'is-done': currentStageIndex > i,
              'is-current': currentStageIndex === i,
            }"
          >{{ s.label }}</span>
        </div>
        <div v-if="waitedText" class="pc-waited">{{ waitedText }}</div>
      </div>
    </div>
    <DialogueInput
      :disabled="isLoading"
      @send="emit('send-message', $event)"
      @stop="emit('stop')"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRequirementStore } from '@/stores/requirement'
import DialogueMessage from './DialogueMessage.vue'
import DialogueInput from './DialogueInput.vue'
import QuestionForm from './QuestionForm.vue'
import ExecutionPanel from './ExecutionPanel.vue'
import PlanConfirmation from './PlanConfirmation.vue'
import type { DialogueMessage as DialogueMessageType } from '@/types/api'
import type { SSEQuestionFormData, SSETraceSummaryData } from '@/types/sse'

const emit = defineEmits<{
  (
    e: 'send-message',
    message: string,
    clarify?: { questions: SSEQuestionFormData['questions']; answers: Record<string, string> }
  ): void
  (e: 'stop'): void
}>()

const bodyRef = ref<HTMLElement | null>(null)
const store = useRequirementStore()

/** 时间戳 "2026-09-26 21:53:06" → "21:53:06" */
function shortTime(ts?: string | null): string {
  if (!ts) return ''
  const m = String(ts).match(/(\d{2}:\d{2}:\d{2})/)
  return m ? m[1] : String(ts)
}

/**
 * 消息时间文案：聚合类消息（轮次 / AC 验收）显示起止区间，普通消息显示单点时间。
 * 卡片头部自己也渲染了一次时间；这里统一补上，保证「所有消息都显示时间」。
 */
function msgTime(msg: DialogueMessageType): string {
  const m = msg as any
  // 轮次卡 / AC 卡：起止时间
  if (m.role === 'iteration_batch' || m.role === 'qa_result') {
    const s = shortTime(m.start_ts ?? m.qa_result?.start_ts)
    const e = shortTime(m.end_ts ?? m.qa_result?.end_ts ?? m.timestamp)
    if (s && e) return `${s} – ${e}`
    return s || e
  }
  return shortTime(m.timestamp)
}

const messages = computed(() => {
  const raw = store.dialogueMessages.filter(
    (m: DialogueMessageType) =>
      m.content !== '__QUESTION_FORM__' && !(m as any).hidden && !(m as any).plan_feedback &&
      // 「0 个操作」幽灵卡片兜底：无论消息从哪条路径进入 store（SSE 实时推送、
      // 历史恢复、chat 响应合并），只要迭代批次没有操作列表就不渲染。
      // useSSE 与后端 sse_reporter 已在源头过滤，这里是最后一条防线。
      // 但「进行中（live）」的轮次卡片即使 tools 暂为空也要渲染——它正实时累积。
      !(m.role === 'iteration_batch' && !((m as any).tools?.length) && (m as any).live !== true) &&
      // 「0 步」验收幽灵卡兜底：一条验收记录若最终没有产生任何步骤，说明这条 AC
      // 根本没被真正执行（浏览器提前退出 / 事件错位），展示成空卡只会让人以为
      // 「质量工程师什么都没做」。后端已不再为 0 步 AC 落卡，这里是最后一道防线。
      !(m.role === 'qa_result' && !(((m as any).qa_result?.steps || []).length) && (m as any).live !== true)
  )

  // 合并连续的同类消息：
  //  1) 连续同一角色的 iteration_batch（coder 的多轮迭代）→ 一张「一次编码回合」卡片，
  //     卡片内部再按轮分层，并支持一键展开/收起全部思考与操作（需求 198 验收反馈：
  //     一轮一张卡时会话框被 20 张卡刷满，反而看不清做了什么）。
  //  2) 同一 AC 编号的多次验收 → 一张「N 次验收」卡（见下），卡内按轮次分层。
  //  3) 连续 tool_call → 一个可展开组（兼容旧版后端/页面刷新时的历史数据）
  //
  // —— QA 验收卡按 AC 编号合并 ——
  // 同一条验收条件在「发现缺陷 → 修复 → 复验」的循环里会被反复验收
  // （需求 198 实测 5 条 AC × 4 轮 = 20 张卡），每张卡头几乎一模一样，
  // 把会话框刷满却看不出「这条现在到底过没过」。
  // 合并后渲染在**该 AC 最后一次出现的位置**：用户读到这里，看到的是终局结论
  // 加上完整历史轮次，而不是散落 4 处的重复卡。
  const qaByAc = new Map<string, DialogueMessageType[]>()
  const qaLastIdx = new Map<string, number>()
  raw.forEach((m: DialogueMessageType, i: number) => {
    if (m.role !== 'qa_result') return
    const id = String((m as any).qa_result?.ac_id || '')
    // ac_id 缺失的脏数据不参与合并：否则所有无编号的卡会被并成一张
    if (!id) return
    if (!qaByAc.has(id)) qaByAc.set(id, [])
    qaByAc.get(id)!.push(m)
    qaLastIdx.set(id, i)
  })

  const buildQaGroup = (rounds: DialogueMessageType[]) => {
    const first: any = rounds[0]
    const last: any = rounds[rounds.length - 1]
    return {
      role: 'qa_result',
      name: last.name || 'Catherine（质量工程师）',
      content: '',
      rounds,
      // 卡头结论取最新一轮：修了几轮之后，用户只关心现在过了没有
      qa_result: { ...(last.qa_result || {}) },
      live: rounds.some((m) => (m as any).live === true),
      start_ts: first.qa_result?.start_ts ?? first.timestamp,
      end_ts: last.qa_result?.end_ts ?? last.timestamp,
      timestamp: last.qa_result?.end_ts ?? last.timestamp,
    } as any
  }

  const grouped: DialogueMessageType[] = []
  let toolBatch: DialogueMessageType[] = []
  let iterBatch: DialogueMessageType[] = []
  let iterName = ''

  const flushTools = () => {
    if (!toolBatch.length) return
    if (toolBatch.length === 1) {
      grouped.push(toolBatch[0])
    } else {
      grouped.push({
        role: 'tool_call',
        name: '工具调用',
        content: '',
        _grouped: true,
        label: `📝 工具调用`,
        items: toolBatch,
      } as any)
    }
    toolBatch = []
  }

  const flushIters = () => {
    if (!iterBatch.length) return
    const first = iterBatch[0]
    const last = iterBatch[iterBatch.length - 1]
    const totalTools = iterBatch.reduce(
      (n, m) => n + (((m as any).tools || []).length), 0
    )
    grouped.push({
      role: 'iteration_batch',
      name: first.name,
      content: `${iterBatch.length} 轮 · ${totalTools} 个操作`,
      _grouped: true,
      rounds: iterBatch,
      start_ts: (first as any).start_ts ?? first.timestamp,
      end_ts: (last as any).end_ts ?? last.timestamp,
      timestamp: (last as any).end_ts ?? last.timestamp,
      // 任一轮仍在实时累积，整张卡就保持「进行中」
      live: iterBatch.some((m) => (m as any).live === true),
    } as any)
    iterBatch = []
    iterName = ''
  }

  for (let ri = 0; ri < raw.length; ri++) {
    const msg = raw[ri]
    if (msg.role === 'iteration_batch') {
      flushTools()
      // 换人即断组：不同角色的轮次不合并
      if (iterBatch.length && (msg.name || '') !== iterName) flushIters()
      if (!iterBatch.length) iterName = msg.name || ''
      iterBatch.push(msg)
      continue
    }
    flushIters()
    if (msg.role === 'tool_call') {
      toolBatch.push(msg)
      continue
    }
    flushTools()

    if (msg.role === 'qa_result') {
      const id = String((msg as any).qa_result?.ac_id || '')
      const rounds = qaByAc.get(id) || [msg]
      // 只验收过一次就保持原样，不引入多余的层级
      if (rounds.length > 1) {
        if (qaLastIdx.get(id) !== ri) continue
        grouped.push(buildQaGroup(rounds))
        continue
      }
    }

    grouped.push(msg)
  }

  flushIters()
  flushTools()

  return grouped
})
const isLoading = computed(() => store.isGenerating)

// ---- 进度可观测化 ----
// 执行阶段（顺序即时间线）：需求分析 → 编码 → 验证 → 修复
const STAGES = [
  { key: 'planning', label: '需求分析' },
  { key: 'coding', label: '编码' },
  { key: 'verifying', label: '验证' },
  { key: 'repairing', label: '修复' },
]

const currentStageIndex = computed(() =>
  STAGES.findIndex((s) => s.key === store.progress.stage)
)

// 后端推送的是动作描述（"正在创建 js/app.js"）；无动作时退化到中性文案，
// 不再显示没有信息量的"AI 正在处理…"。
const activityText = computed(() => store.progress.currentAgent || '正在处理')

// 静默期计时：LLM 挂起时后端可能数十秒无任何事件，这里用本地秒级心跳
// 把"这一步已经跑了多久"显式说出来，避免用户只能面对一个不动的界面。
const now = ref(Date.now())
let tickTimer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  tickTimer = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})
onUnmounted(() => {
  if (tickTimer) clearInterval(tickTimer)
})

const waitedText = computed(() => {
  const updatedAt = store.progress.updatedAt
  if (!updatedAt) return ''
  const secs = Math.max(0, Math.floor((now.value - updatedAt) / 1000))
  // 15s 以内是正常节奏，不必提示；超过后用户的真实疑问是"是不是卡住了"
  if (secs < 15) return ''
  if (secs < 60) return `已等待 ${secs} 秒`
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return `已等待 ${m} 分 ${s} 秒`
})

// Access SSE-triggered state from the store
const questionForm = computed(() => store.questionForm)
const questionFormMode = computed(() =>
  store.pendingChatClarification ? 'chat' : 'requirement'
)
const traceSummary = computed(() => store._traceSummary as SSETraceSummaryData | null)

// Plan confirmation
const showPlanConfirmation = computed(() => store.planStatus === 'needs_confirmation')
const specData = computed(() => store._specData)
/**
 * 确认卡片该插在哪：紧跟本轮 TL 分析消息之后，与后端落库位置一致。
 *
 * 实时链路的对话消息不携带 plan 字段（SSE dialogue 事件只有纯文本），只能靠 spec 事件
 * 到达时记下的下标；刷新恢复链路的消息来自 DB、带 plan，可以直接就地定位 —— 那时
 * `_specInsertIndex` 早被重置为 null，只靠它会把卡片甩到消息流末尾。
 */
function planCardInsertIndex(): number {
  const msgs = store.dialogueMessages
  for (let i = msgs.length - 1; i >= 0; i--) {
    if ((msgs[i] as any).plan) return i + 1
  }
  const recorded = store._specInsertIndex
  if (typeof recorded === 'number' && recorded >= 0 && recorded <= msgs.length) {
    return recorded
  }
  return msgs.length
}

// 确认后把计划卡固定成消息流里的一条（后端已持久化同样一条）。
//
// 为什么用 watch 而不是让 PlanConfirmation emit 回来：store.confirmPlan 一返回，
// planStatus 就切到 confirmed，浮层卡当帧被卸载，而 Vue 会丢弃已卸载组件的 emit
// （runtime-core `emit()` 首行即 `if (instance.isUnmounted) return`）。
// 之前那样写的结果是「框没了，消息也没留下」，只有刷新/断线补齐才补上这条卡。
watch(
  () => store.planStatus,
  (now, before) => {
    if (now !== 'confirmed' || before !== 'needs_confirmation') return
    // 断线补齐可能已经把后端那条卡片带进来了，不能插成两张
    if (store.dialogueMessages.some((m) => (m as any).plan_confirmed)) return
    // 内容与刚才那张待确认卡片完全一致 —— 确认是同一张卡的状态切换，不是另做一张摘要
    const spec = specData.value
    store.dialogueMessages.splice(planCardInsertIndex(), 0, {
      role: 'user',
      name: '用户',
      content: '已确认开发计划，开始编码',
      plan_confirmed: {
        features: spec?.features || [],
        tech_stack: spec?.tech_stack || {},
        file_structure: spec?.file_structure || [],
        data_model: spec?.data_model || '',
        complexity: spec?.complexity || 'S',
      },
    } as any)
  }
)

async function onQuestionSubmitted(answers?: Record<string, string>) {
  if (store.pendingChatClarification && answers) {
    // Chat 模式澄清：拼接答案后重新发送
    if (answers._skip) {
      // 用户跳过，用原始消息继续
      const { originalMessage } = store.pendingChatClarification
      store.pendingChatClarification = null
      store.questionForm = null
      emit('send-message', originalMessage)
    } else {
      // 拼接答案到原始消息（LLM 上下文用完整消息，展示用已提交卡片）
      const questions = store.questionForm?.questions || []
      const answerText = Object.entries(answers)
        .filter(([, v]) => v)
        .map(([q, a]) => `${q}: ${a}`)
        .join('；')
      const { originalMessage } = store.pendingChatClarification
      const enrichedMessage = `[用户补充说明]\n${answerText}\n\n原始修改意见：${originalMessage}`

      store.pendingChatClarification = null
      store.questionForm = null

      // 已完成表单作为特殊 user 消息进入消息流（后端持久化同样一条）
      store.addDialogueMessage({
        role: 'user',
        name: '用户',
        content: answerText || '已确认',
        question_form: { questions, submitted: true, answers: { ...answers } },
      })

      emit('send-message', enrichedMessage, { questions, answers: { ...answers } })
    }
  } else {
    // 新需求 SOP 模式：QuestionForm 内部已调用 /clarify 并把已完成表单落成消息流卡片
  }
}

// Auto-scroll to bottom when new messages arrive
watch(
  () => store.dialogueMessages.length,
  () => {
    nextTick(() => {
      if (bodyRef.value) {
        bodyRef.value.scrollTop = bodyRef.value.scrollHeight
      }
    })
  }
)
</script>

<style scoped>
.dialogue-panel {
  flex: 0 0 40%;
  display: flex;
  flex-direction: column;
  background: var(--surface);
  min-width: 0;
}

.dialogue-header {
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
  flex-shrink: 0;
}

.dialogue-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dialogue-body::-webkit-scrollbar {
  width: 6px;
}

.dialogue-body::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 3px;
}

/* 不生成盒模型：让内部 .msg 继续作为父级 flex 的直接参与项（保留左右对齐），
   同时允许时间条作为同级 flex item 统一渲染 */
.msg-wrapper {
  display: contents;
}

.msg-time {
  align-self: center;
  font-size: 11px;
  color: #9ca3af;
  font-variant-numeric: tabular-nums;
  margin: -8px 0 0;
  letter-spacing: .2px;
}

.progress-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 16px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 12px;
  align-self: stretch;
  margin-top: auto;
}

.pc-head {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.pc-action {
  font-size: 13px;
  color: var(--fg);
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pc-stages {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.pc-stage {
  font-size: 11px;
  color: var(--muted);
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: transparent;
}

.pc-stage.is-done {
  color: var(--muted);
  border-color: var(--border);
  text-decoration: line-through;
  opacity: 0.6;
}

.pc-stage.is-current {
  color: var(--accent);
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
}

.pc-waited {
  font-size: 12px;
  color: var(--muted);
}

.lb-dots {
  display: flex;
  gap: 4px;
}

.lb-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: lb-bounce 0.6s infinite alternate;
}

.lb-dot:nth-child(2) {
  animation-delay: 0.2s;
}

.lb-dot:nth-child(3) {
  animation-delay: 0.4s;
}
</style>
