<template>
  <div class="publish-panel">
    <!-- 加载态：进入 TAB 首屏拉取 by-requirement，命中前都按这个渲染 -->
    <div v-if="loading" class="publish-card">
      <div class="pc-head">
        <h3>发布管理</h3>
        <span class="pc-status loading">
          <span class="pc-dot"></span>加载中
        </span>
      </div>
      <div class="pc-body">
        <p class="pc-loading-hint">正在读取发布状态…</p>
        <div class="pc-skel skel-long"></div>
        <div class="pc-skel skel-mid"></div>
        <div class="pc-skel skel-short"></div>
        <div class="pc-skel-url"></div>
        <button class="btn-publish" disabled>查询中…</button>
      </div>
    </div>

    <!-- 已发布但 url 不可用（域名未配置）：保留产物，主操作降级 -->
    <div v-else-if="result && isPublishedButUnavailable" class="publish-card">
      <div class="pc-head">
        <h3>发布管理</h3>
        <span class="pc-status warn">
          <span class="pc-dot"></span>已保存 · 链接不可用
        </span>
      </div>
      <div class="pc-body">
        <div class="pc-warn-box">
          <span class="pc-warn-icon"></span>
          <div class="pc-warn-col">
            <strong>当前环境未配置发布域名</strong>
            <p>产物已落盘，但当前环境没有可访问的发布域名，链接不可用。配置 PUBLISH_APEX 并重启服务后，同一需求的链接即可访问。</p>
          </div>
        </div>
        <div class="pc-meta">
          <span>版本 v{{ result.version }}</span>
          <span class="pc-sep"></span>
          <span>{{ formatDateTime(result.updated_at) }}</span>
          <span class="pc-sep"></span>
          <span>{{ result.view_count ?? 0 }} 次访问</span>
        </div>
        <div class="pc-url-label">发布链接</div>
        <div class="pc-url-box disabled">
          <span class="pc-url-text muted">— 当前环境无可用域名 —</span>
        </div>
        <button class="btn-publish btn-secondary" :disabled="publishing || unpublishing || !canPublish" @click="onPublish">
          {{ publishing ? '发布中…' : '重新发布（仍不可访问）' }}
        </button>
        <div class="pc-actions">
          <span></span>
          <button class="pc-link pc-danger" :disabled="unpublishing" @click="onUnpublish">
            {{ unpublishing ? '取消中…' : '取消发布' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 已发布（主态，URL 正常） -->
    <div v-else-if="result" class="publish-card">
      <div class="pc-head">
        <h3>发布管理</h3>
        <span class="pc-status online">
          <span class="pc-dot"></span>已发布 · 在线
        </span>
      </div>
      <div class="pc-body">
        <div class="pc-meta">
          <span>版本 v{{ result.version }}</span>
          <span class="pc-sep"></span>
          <span>{{ formatDateTime(result.updated_at) }}</span>
          <span class="pc-sep"></span>
          <span>{{ result.view_count ?? 0 }} 次访问</span>
        </div>
        <div class="pc-url-label">发布链接</div>
        <div class="pc-url-box">
          <a class="pc-url" :href="openUrl" target="_blank" rel="noopener">{{ openUrl }}</a>
          <button class="pc-copy" :disabled="!openUrl" @click="copyUrl">复制</button>
        </div>
        <a class="btn-publish" :href="openUrl" target="_blank" rel="noopener">打开站点</a>

        <!-- 创意市集：上架是 opt-in，默认不勾（尊重 unlisted 的既有预期） -->
        <div class="pc-market">
          <label class="pc-switch">
            <input type="checkbox" v-model="marketListed" @change="syncMarket" />
            <span>同步到创意市集</span>
          </label>
          <p class="pc-market-hint">
            上架后会出现在市集列表，访客无需登录就能看见并点赞。
          </p>
          <div v-if="marketListed" class="pc-market-extra">
            <input class="pc-note" type="text" maxlength="120" v-model="authorNote"
                   placeholder="一句话介绍（选填）" @change="syncMarket" />
            <select class="pc-note" v-model="category" @change="syncMarket">
              <option value="">分类（选填）</option>
              <option v-for="c in categoryOptions" :key="c.value" :value="c.value">
                {{ c.label }}
              </option>
            </select>
            <label class="pc-switch small">
              <input type="checkbox" v-model="badgeEnabled" @change="syncMarket" />
              <span>站点右下角显示来源入口</span>
            </label>

            <!-- 封面：默认就是首页截图，这里只是给作者一个「我另挑一张」的入口。
                 预览直接吃缩略图端点（服务时优先出封面），所以看到的就是市集里
                 实际展示的那张图，不存在「上传成功但列表没变」的错觉。 -->
            <div class="pc-cover">
              <div class="pc-cover-preview">
                <img v-if="!previewFailed" class="pc-cover-img" :src="previewUrl" alt="封面"
                     @error="previewFailed = true" />
                <span v-else class="pc-cover-ph">预览不可用</span>
              </div>
              <div class="pc-cover-ops">
                <label class="pc-cover-btn">
                  {{ hasCover ? '更换封面' : '上传封面' }}
                  <input class="pc-file" type="file" accept="image/png,image/jpeg"
                         @change="onCoverPick" />
                </label>
                <button v-if="hasCover" class="pc-link" :disabled="coverBusy" @click="removeCover">
                  移除封面
                </button>
                <p class="pc-cover-hint">
                  不上传就用首页截图。PNG / JPEG，2 MB 以内。
                </p>
              </div>
            </div>
          </div>
          <p v-if="marketError || coverError" class="pc-market-err">{{ marketError || coverError }}</p>
        </div>

        <div class="pc-actions">
          <button class="pc-link" :disabled="publishing || unpublishing || !canPublish" @click="onPublish">
            {{ publishing ? '发布中…' : '重新发布（新版本）' }}
          </button>
          <button class="pc-link pc-danger" :disabled="unpublishing" @click="onUnpublish">
            {{ unpublishing ? '取消中…' : '取消发布' }}
          </button>
        </div>
        <p v-if="!canPublish" class="pc-gate-hint block">
          {{ gate.label }}——发布新版本同样需要一次通过 QA 验收的生成结果。
        </p>
      </div>
    </div>

    <!-- 未发布：发布前最后一步（设计稿 2.5） -->
    <div v-else class="publish-card">
      <div class="pc-pre-head">
        <h3>发布前最后一步</h3>
        <p class="pc-pre-sub">
          点「发布」，就会拿到一个独立网址。链接保持不变，改了再发布也还是同一个。
        </p>
      </div>
      <div class="pc-body">
        <!-- 就绪状态条：QA 结果直接说人话，不再让用户自己拼状态含义 -->
        <div :class="['pc-gate-bar', gate.tone]">
          <span class="pc-gate-dot"></span>
          <span>{{ gate.barText }}</span>
          <span v-if="assetSummary" class="pc-gate-files">{{ assetSummary }}</span>
        </div>
        <p v-if="gate.hint" :class="['pc-gate-hint', gate.tone]">{{ gate.hint }}</p>

        <!-- 谁能看见：发布即生成链接，链接本来就是给别人的，
             所以这里只有「先私享再定」和「直接上市集」两档，没有"仅自己" -->
        <div class="pc-field-label">谁能看见</div>
        <div class="pc-visibility">
          <button
            type="button"
            :class="['pc-vis-card', { picked: visibilityChoice === 'link' }]"
            @click="visibilityChoice = 'link'"
          >
            <span class="pc-vis-radio"></span>
            <span class="pc-vis-body">
              <span class="pc-vis-title">仅链接可访问（不上市集）</span>
              <span class="pc-vis-desc">推荐 · 分享给别人前自己先用</span>
            </span>
          </button>
          <button
            type="button"
            :class="['pc-vis-card', { picked: visibilityChoice === 'market' }]"
            @click="visibilityChoice = 'market'"
          >
            <span class="pc-vis-radio"></span>
            <span class="pc-vis-body">
              <span class="pc-vis-title">公开 · 也同步到创意市集</span>
              <span class="pc-vis-desc">上架后可随时在市集撤回</span>
            </span>
          </button>
        </div>

        <button class="btn-publish" :disabled="publishing || !canPublish" @click="onPublish">
          {{ publishing ? '发布中…' : '发布' }}
        </button>
        <p class="pc-tip">
          发布后会生成一个独立网址，访问者无需登录即可打开；上架市集可以随时撤回。
        </p>
        <button v-if="gate.retryable" class="pc-link pc-retry" :disabled="store.isGenerating" @click="onRetry">
          {{ store.isGenerating ? '正在重新生成…' : '重新生成代码' }}
        </button>
      </div>
    </div>

    <!-- 全局动作错误（发布/取消失败） -->
    <p v-if="error" class="publish-error">{{ error }}</p>
    <!-- 首屏拉取错误（by-requirement 恒定 200，故这里只可能是网络/服务/JWT 类真错误） -->
    <p v-if="loadError" class="publish-error">{{ loadError }}</p>
    <!-- 后端返回的 warnings（域名未配置等）：与设计稿 D 态警示框互补，不重复渲染 -->
    <ul v-if="warnings.length" class="publish-warnings">
      <li v-for="(w, i) in warnings" :key="i">{{ w }}</li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRequirementStore } from '@/stores/requirement'
import { useToast } from '@/composables/useToast'
import { useApi } from '@/composables/useApi'
import { canPublish as canPublishNow, IN_PROGRESS_STATUSES } from '@/utils/publishGate'

interface PublishResult {
  // 仅 by-requirement 端点携带（永远 200）；POST /api/publish 不会带此字段。
  // 为 false 时表示「该需求从未发布 / 不属于当前用户 / 不存在」三种情形之一
  // —— 后端不区分以避免响应差异泄露资源存在性。
  published?: boolean
  slug: string
  version: number
  // 以下三项是平台内部信号，**刻意不在界面上暴露**（保留字段仅为对齐后端响应
  // 形状、便于排障时打印）：visibility 目前恒为 unlisted，verify_status 反映的是
  // 发布后复验的健康度，不是用户需要理解的概念。别顺手把它们加回模板。
  visibility: string
  verify_status: string
  current_hash: string
  published_host: string | null
  url: string | null
  warnings?: string[]
  // 持久化新增字段（by-requirement 端点会一并返回；模板暂不展示
  // view_count / created_at，但存进 result 便于后续扩展）
  view_count?: number
  created_at?: string | null
  updated_at?: string | null
  // 创意市集（作者本人可见）
  listed?: boolean
  author_note?: string
  badge_enabled?: boolean
  category?: string
  cover?: boolean
}

const emit = defineEmits<{ resume: [] }>()

const store = useRequirementStore()
const { show } = useToast()
const { api } = useApi()

const reqId = computed(() => store.currentRequirement?.id ?? null)
const publishing = ref(false)
const unpublishing = ref(false)
// 首屏加载态：true → 渲染骨架屏；false → 根据 result 决定下一步
const loading = ref(true)
// 拉取失败（非 404）时的错误提示，区别于发布/取消动作的 error
const loadError = ref('')
// 持久化来源：进入 TAB 时拉 by-requirement，命中则填充；发布/取消动作再覆盖
const result = ref<PublishResult | null>(null)
const error = ref('')

// 唯一真值来源是后端返回的 url。这里**不做任何前端兜底拼接**：
// 曾经在 url 为空时自拼 http://<slug>.127.0.0.1.nip.io:<port>/，但 nip.io 只是
// DNS 通配，Host 路由仍要求 PUBLISH_APEX 被正确配置 —— 那个兜底点开必然落到
// 主站 SPA（要求登录 + 显示首页），用户会当成「一键发布坏了」。
const openUrl = computed(() => result.value?.url || '')
// 域名未配置 = url 为空 + 已发布过（产物已落盘但没有可访问域名）
const isPublishedButUnavailable = computed(
  () => !!result.value && !openUrl.value
)

const warnings = computed(() => result.value?.warnings ?? [])

// ===== 谁能看见（未发布态选择）=====
// 发布本身就会生成公开链接，没有"仅自己"一档：链接不可访问等于没发布。
// link = 只把链接给特定的人；market = 发布成功后顺手上架创意市集。
const visibilityChoice = ref<'link' | 'market'>('link')

// ===== 创意市集 =====
// 上架一律 opt-in：默认不勾。现状所有站点都是 unlisted，用户预期是"只分享链接"，
// 默认搬进公共列表是对既有预期的背叛。
const marketListed = ref(false)
const authorNote = ref('')
const badgeEnabled = ref(true)
const category = ref('')
const marketError = ref('')

// ===== 封面 =====
// 预览 = 缩略图端点当前实际会出的那张图（有封面出封面，没有就出首页截图）。
// 换封面是作者动作、频率极低，靠 coverSeq 手动打缓存钉，比 no-cache 更可靠。
const hasCover = ref(false)
const coverSeq = ref(0)
const coverBusy = ref(false)
const coverError = ref('')
const previewFailed = ref(false)
const previewUrl = computed(() => {
  const slug = result.value?.slug
  if (!slug) return ''
  return `/api/market/thumbs/${slug}.png?v=${coverSeq.value}`
})

// 分类枚举与后端 CATEGORIES 保持一致（后端非法值会静默归为未分类，不报错）
const categoryOptions = [
  { value: 'game', label: '游戏' },
  { value: 'tool', label: '工具' },
  { value: 'admin', label: '后台' },
  { value: 'landing', label: '展示' },
  { value: 'other', label: '其它' },
]

watch(
  result,
  (r) => {
    marketListed.value = !!r?.listed
    authorNote.value = r?.author_note || ''
    badgeEnabled.value = r?.badge_enabled !== false
    category.value = r?.category || ''
    hasCover.value = !!r?.cover
    coverSeq.value += 1
    previewFailed.value = false
  },
  { immediate: true }
)

async function onCoverPick(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  // 清空 value：否则连续选同一个文件不会再触发 change，看起来像「点了没反应」
  input.value = ''
  if (!file) return
  const slug = result.value?.slug
  if (!slug) return

  // 客户端先挡一道，只是为了让作者立刻看到原因；真正的校验在服务端（魔数 + 2MB）
  if (file.size > 2 * 1024 * 1024) {
    coverError.value = '封面不能超过 2 MB'
    return
  }
  coverError.value = ''
  coverBusy.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    await api(`/api/market/sites/${slug}/cover`, { method: 'POST', body: fd })
    hasCover.value = true
    coverSeq.value += 1
    previewFailed.value = false
    show('封面已更新', 'success')
  } catch (err) {
    coverError.value = err instanceof Error ? err.message : '上传失败'
  } finally {
    coverBusy.value = false
  }
}

async function removeCover() {
  const slug = result.value?.slug
  if (!slug) return
  coverError.value = ''
  coverBusy.value = true
  try {
    await api(`/api/market/sites/${slug}/cover`, { method: 'DELETE' })
    hasCover.value = false
    coverSeq.value += 1
    previewFailed.value = false
    show('已恢复为首页截图', 'success')
  } catch (err) {
    coverError.value = err instanceof Error ? err.message : '移除失败'
  } finally {
    coverBusy.value = false
  }
}

async function syncMarket() {
  const slug = result.value?.slug
  if (!slug) return
  marketError.value = ''
  // 失败必须把开关回滚：v-model 已经把界面改成"已上架"，只写一行错误文案的话
  // 界面显示成功、后端没保存 —— 这正是「点了同步、市集里却没有」的观感来源。
  // 文本类字段（介绍/分类）保留用户输入，方便他改完重试。
  const prev = { listed: marketListed.value, badge: badgeEnabled.value }
  try {
    await api(`/api/publish/${slug}/market`, {
      method: 'PATCH',
      body: JSON.stringify({
        listed: marketListed.value,
        author_note: authorNote.value,
        badge_enabled: badgeEnabled.value,
        category: category.value,
      }),
    })
    show(marketListed.value ? '已同步到创意市集' : '已从创意市集撤下', 'success')
  } catch (e) {
    marketListed.value = prev.listed
    badgeEnabled.value = prev.badge
    marketError.value = e instanceof Error ? e.message : '保存失败'
  }
}

// 「index.html · 共 N 个文件」：按需求实际的代码文件数算。
// 此前这一行是硬编码的「index.html · 3 个资源」——文件数不是 3 时就是在给用户
// 报错误信息，且「资源」与「文件」含义含混。无文件时不渲染这一行。
const assetSummary = computed(() => {
  const names = store.producedFiles
  if (!names.length) return ''
  const entry = names.includes('index.html') ? 'index.html' : names[0]
  return `${entry} · 共 ${names.length} 个文件`
})

// ===== 发布门禁 =====
// 唯一放行条件：需求通过了 QA 验收（后端把 verify_passed 为真的需求标成 'finished'）
// 并且产物确实存在。此前按钮只看「有没有需求 ID」，元信息卡还无条件写着
// 「代码已就绪，可发布」——需求 162 一个文件都没生成，卡片照样宣称就绪。
const reqStatus = computed(() => store.currentRequirement?.status ?? null)
// 产物判定用 store 的并集（API 快照 ∪ SSE 实时增量）：详情接口的 code_files
// 是进页面那一刻的快照，SSE 的 code 事件只写进实时映射。只看快照时，全新需求
// 整轮生成期间 hasFiles 恒为 false —— QA 验收都过了，这里仍然是「尚未生成代码」，
// 必须手动刷新页面才能发布（req 202 实测）。
const hasFiles = computed(() => store.hasProducedFiles)
// 门禁判定与「有产物」判定都收敛到纯函数（utils/publishGate.ts），
// 有自动化断言：npm run check:gate
const canPublish = computed(() => canPublishNow(reqStatus.value, store.producedFiles))
const inProgress = computed(() => IN_PROGRESS_STATUSES.includes(reqStatus.value as any))

interface Gate {
  tone: 'ready' | 'wait' | 'block'
  label: string
  /** 就绪状态条文案（未发布态横条用） */
  barText: string
  hint: string
  /** 能否就地重试生成（仅失败且无产物时） */
  retryable: boolean
}

const gate = computed<Gate>(() => {
  if (canPublish.value) {
    return {
      tone: 'ready',
      label: '代码已就绪，可发布',
      barText: 'QA 全部通过 · 代码已就绪，可以发布',
      hint: '',
      retryable: false,
    }
  }
  if (inProgress.value) {
    return {
      tone: 'wait',
      label: '代码生成中',
      barText: '代码生成中，等 QA 验收通过后就能发布',
      hint: '生成完成并通过 QA 验收后即可发布。',
      retryable: false,
    }
  }
  if (!hasFiles.value) {
    return {
      tone: 'block',
      label: '尚未生成代码',
      barText: '这次生成没有产出任何代码文件，还不能发布',
      hint: '这次生成没有产出任何代码文件，发布需要一次通过 QA 验收的生成结果。',
      retryable: reqStatus.value === 'failed',
    }
  }
  if (reqStatus.value === 'needs_user_input') {
    return {
      tone: 'block',
      label: '存在未解决的关键缺陷',
      barText: 'QA 发现关键缺陷并拦截了交付，先在对话里修复吧',
      hint: 'QA 验收发现了关键缺陷并拦截了交付，建议先在对话中修复再通过验收。',
      retryable: false,
    }
  }
  return {
    tone: 'block',
    label: '代码已生成，QA 验收未通过',
    barText: '产物可用，但 QA 验收未全部通过',
    hint: '产物可用，但验收未全部通过；通过验收后再发布能让拿到链接的人获得完整体验。',
    retryable: false,
  }
})

// 进入 TAB 时拉一次发布状态：解决「刷新后只剩一个发布按钮」的持久化问题。
// 后端 by-requirement 永远返回 200 —— `published: false` 表示「未发布 / 不属于我 / 不存在」，
// 这种情况下不当作错误（避免 Chrome Network 红字噪音）。
async function loadPublishState() {
  if (!reqId.value) {
    loading.value = false
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    const data = await api<PublishResult>(
      `/api/publish/by-requirement/${reqId.value}`
    )
    // `published: false` 是 by-requirement 的「未发布」正常态，
    // 其他字段缺失（slug=undefined）说明这不是已发布记录 —— 当未发布处理
    if (data.published === false) {
      result.value = null
    } else {
      result.value = data
    }
  } catch (e) {
    // 真错误（网络/服务挂了/JWT 失效）才显示给用户
    loadError.value = (e as Error).message || '加载失败'
    result.value = null
  } finally {
    loading.value = false
  }
}

// 需求切换时（比如路由参数变化、resume）重新拉取，避免旧数据残留
watch(reqId, (newId) => {
  if (newId) loadPublishState()
})

onMounted(() => {
  loadPublishState()
})

// 时间格式化：created_at 是 ISO 字符串，渲染成「YYYY-MM-DD HH:MM」
function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function onPublish() {
  if (!reqId.value) return
  publishing.value = true
  error.value = ''
  try {
    const data = await api<PublishResult>('/api/publish', {
      method: 'POST',
      body: JSON.stringify({ requirement_id: reqId.value, visibility: 'unlisted' }),
    })
    result.value = data
    if (data.url) {
      show('发布成功', 'success')
      // 发布前选了「公开」：拿到 slug 后立即上架创意市集（listed 是市集侧开关）
      if (visibilityChoice.value === 'market' && data.slug) {
        try {
          marketListed.value = true
          await syncMarket()
        } catch {
          // syncMarket 内部已回滚开关并写 marketError，这里不再叠加错误
        }
      }
    } else {
      show('已保存产物，但当前没有可访问的发布域名', 'error')
    }
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    publishing.value = false
  }
}

// 生成失败且无产物时，就地再跑一次。重新入队与 SSE 重连都由外层 DetailView 的
// onResume 统一处理（它同时服务顶部的中断/失败提示条），这里只发信号，
// 避免两处各写一份 resume 逻辑、各弹一次 toast。
function onRetry() {
  emit('resume')
}

async function onUnpublish() {
  if (!result.value) return
  unpublishing.value = true
  error.value = ''
  try {
    await api(`/api/publish/${result.value.slug}/unpublish`, { method: 'POST' })
    result.value = null
    show('已取消发布', 'success')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    unpublishing.value = false
  }
}

async function copyUrl() {
  if (!openUrl.value) return
  try {
    await navigator.clipboard.writeText(openUrl.value)
    show('链接已复制', 'success')
  } catch {
    error.value = '复制失败，请手动复制'
  }
}
</script>

<style scoped>
/* ===== 容器：铺在右侧深色工作台上，全部走 wb token ===== */
.publish-panel {
  padding: 12px;
  color: var(--wb-fg);
  font-family: var(--font-body);
}

.publish-card {
  background: var(--wb-surface);
  border: 1px solid var(--wb-border);
  border-radius: 10px;
  overflow: hidden;
}

/* ===== 标题栏：与 spec-header / task-header 同模式 ===== */
.pc-head {
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--wb-fg);
  border-bottom: 1px solid var(--wb-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pc-head h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--wb-fg);
}

/* ===== 状态 pill（标题栏右侧） ===== */
.pc-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 500;
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--wb-hover);
  color: #6b6b6b;
}
.pc-status.online {
  background: #dcfce7;
  color: #15803d;
}
.pc-status.warn {
  background: #fef3c7;
  color: #a16207;
}
.pc-status.loading {
  background: var(--wb-hover);
  color: #6b6b6b;
}
.pc-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: currentColor;
  flex-shrink: 0;
}
.pc-status.online .pc-dot { background: #16a34a; }
.pc-status.warn .pc-dot { background: #d97706; }
.pc-status.loading .pc-dot {
  background: #9ca3af;
  animation: pc-pulse 1.2s ease-in-out infinite;
}
.pc-dot.ready {
  background: #16a34a;
}
@keyframes pc-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}

/* ===== 卡片主体 ===== */
.pc-body {
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* ===== 发布前最后一步：标题区 ===== */
.pc-pre-head {
  padding: 14px 18px 12px;
  border-bottom: 1px solid var(--wb-border);
}

.pc-pre-head h3 {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 700;
  color: var(--wb-fg);
}

.pc-pre-sub {
  margin: 0;
  font-size: 12.5px;
  color: var(--wb-muted);
  line-height: 1.6;
}

/* ===== 就绪状态条 ===== */
.pc-gate-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 12.5px;
  font-weight: 500;
}

.pc-gate-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: currentColor;
  flex-shrink: 0;
}

.pc-gate-files {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 11px;
  opacity: 0.75;
}

.pc-gate-bar.ready {
  background: rgba(76, 138, 90, 0.16);
  border: 1px solid rgba(76, 138, 90, 0.4);
  color: #7fbf8c;
}

.pc-gate-bar.wait {
  background: rgba(185, 138, 46, 0.14);
  border: 1px solid rgba(185, 138, 46, 0.4);
  color: #d8b46a;
}

.pc-gate-bar.block {
  background: rgba(192, 84, 74, 0.14);
  border: 1px solid rgba(192, 84, 74, 0.4);
  color: #e08a80;
}

/* ===== 谁能看见 ===== */
.pc-field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--wb-fg);
  margin-top: 2px;
}

.pc-visibility {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.pc-vis-card {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid var(--wb-border);
  background: var(--wb-elevated);
  cursor: pointer;
  text-align: left;
  font-family: var(--font-body);
  transition: border-color 0.15s, background 0.15s;
}

.pc-vis-card:hover {
  border-color: var(--wb-muted);
}

.pc-vis-card.picked {
  border-color: var(--accent);
  background: rgba(207, 106, 95, 0.1);
}

.pc-vis-radio {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 1.5px solid var(--wb-muted);
  flex-shrink: 0;
  margin-top: 2px;
  position: relative;
}

.pc-vis-card.picked .pc-vis-radio {
  border-color: var(--accent);
}

.pc-vis-card.picked .pc-vis-radio::after {
  content: '';
  position: absolute;
  inset: 2.5px;
  border-radius: 50%;
  background: var(--accent);
}

.pc-vis-body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.pc-vis-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--wb-fg);
}

.pc-vis-desc {
  font-size: 11px;
  color: var(--wb-muted);
  line-height: 1.5;
}

/* 介绍 / 提示段落 */
.pc-intro,
.pc-loading-hint,
.pc-tip {
  margin: 0;
  font-size: 13px;
  color: var(--wb-fg);
  line-height: 1.6;
}
.pc-loading-hint { color: var(--wb-muted); }
.pc-tip { color: var(--wb-muted); }

/* ===== 元信息行（版本 · 时间 · 访问数）===== */
.pc-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--wb-muted);
}
.pc-meta span { white-space: nowrap; }
.pc-sep {
  width: 1px !important;
  height: 12px;
  background: var(--wb-border);
  display: inline-block;
}

/* ===== 元信息卡（绿色「代码已就绪」）===== */
.pc-meta-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--wb-elevated);
  border: 1px solid var(--wb-border);
  font-size: 12px;
}
.pc-meta-sub {
  color: var(--wb-muted);
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 12px;
}

/* ===== 门禁态：ready=通过验收可发布 / wait=生成中 / block=未过验收 ===== */
.pc-meta-card.wait {
  background: #fffbeb;
  border-color: #fde68a;
}
.pc-meta-card.block {
  background: #fff5f5;
  border-color: #fecaca;
}
.pc-dot.wait { background: #d97706; }
.pc-dot.block { background: #dc2626; }
.pc-meta-label { font-weight: 500; }
.pc-meta-label.ready { color: #16a34a; }
.pc-meta-label.wait { color: #a16207; }
.pc-meta-label.block { color: #b91c1c; }
.pc-gate-hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
}
.pc-gate-hint.wait { color: #a16207; }
.pc-gate-hint.block { color: #b91c1c; }

/* ===== 创意市集 ===== */
.pc-market {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--wb-border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pc-switch {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  color: var(--wb-fg);
  cursor: pointer;
}
.pc-switch.small {
  font-size: 12px;
  color: var(--wb-muted);
}
.pc-market-hint {
  margin: 0;
  font-size: 12px;
  color: var(--wb-muted);
  line-height: 1.6;
}
.pc-market-extra {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pc-note {
  width: 100%;
  padding: 7px 10px;
  border: 1px solid var(--wb-border);
  border-radius: 8px;
  background: var(--wb-elevated);
  color: var(--wb-fg);
  font-size: 13px;
}
.pc-note:focus {
  outline: none;
  border-color: var(--accent);
}
.pc-market-err {
  margin: 0;
  font-size: 12px;
  color: var(--color-danger);
}

/* ===== 封面 ===== */
.pc-cover {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.pc-cover-preview {
  width: 116px;
  height: 68px;
  flex-shrink: 0;
  border-radius: 8px;
  border: 1px solid var(--wb-border);
  background: var(--wb-elevated);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}
.pc-cover-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.pc-cover-ph {
  font-size: 11px;
  color: var(--wb-muted);
}
.pc-cover-ops {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
}
.pc-cover-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  padding: 5px 12px;
  border: 1px solid var(--wb-border);
  border-radius: 8px;
  background: var(--wb-surface);
  color: var(--wb-fg);
  font-size: 12px;
  cursor: pointer;
}
.pc-cover-btn:hover { border-color: var(--accent); color: var(--accent); }
/* 原生 file input 藏起来（样式不可控），点 label 即触发 */
.pc-file {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
  width: 100%;
}
.pc-cover-hint {
  margin: 0;
  font-size: 11px;
  color: var(--wb-muted);
  line-height: 1.6;
}

.pc-retry {
  align-self: flex-start;
  color: var(--accent);
}

/* ===== URL 区 ===== */
.pc-url-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--wb-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.pc-url-box {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--wb-elevated);
  border: 1px solid var(--wb-border);
}
.pc-url-box.disabled {
  background: var(--wb-elevated);
  border-style: dashed;
}
.pc-url {
  flex: 1;
  min-width: 0;
  color: var(--accent);
  font-family: var(--font-mono);
  font-size: 13px;
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-url:hover { text-decoration: underline; }
.pc-url-text.muted {
  color: var(--wb-muted);
  font-size: 12px;
}
.pc-copy {
  border: 1px solid var(--wb-border);
  background: var(--wb-surface);
  color: var(--wb-fg);
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
}
.pc-copy:hover { background: var(--wb-elevated); }
.pc-copy:disabled { opacity: 0.5; cursor: not-allowed; }

/* ===== 警示框（域名未配置）===== */
.pc-warn-box {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #fffbeb;
  border: 1px solid #fde68a;
  align-items: flex-start;
}
.pc-warn-icon {
  width: 14px;
  height: 14px;
  border-radius: 999px;
  background: #d97706;
  flex-shrink: 0;
  margin-top: 2px;
}
.pc-warn-col { flex: 1; min-width: 0; }
.pc-warn-col strong {
  display: block;
  font-size: 12px;
  color: #92400e;
  margin-bottom: 4px;
}
.pc-warn-col p {
  margin: 0;
  font-size: 12px;
  color: #a16207;
  line-height: 1.6;
}

/* ===== 主按钮（accent 填充）===== */
.btn-publish {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 10px 22px;
  border-radius: 8px;
  border: none;
  background: var(--accent);
  color: var(--color-on-accent);
  font-size: 13px;
  font-weight: 600;
  font-family: var(--font-body);
  cursor: pointer;
  text-decoration: none;
}
.btn-publish:hover:not(:disabled) { filter: brightness(1.06); }
.btn-publish:disabled { opacity: 0.5; cursor: not-allowed; }
/* 已发布但 url 不可用态：灰底，主操作降级 */
.btn-publish.btn-secondary {
  background: var(--wb-hover);
  color: var(--wb-muted);
  border: 1px solid var(--wb-border);
}

/* ===== 次操作（重新发布 / 取消发布）===== */
.pc-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: -2px;
}
.pc-link {
  background: none;
  border: none;
  padding: 0;
  font-family: var(--font-body);
  font-size: 12px;
  color: var(--wb-muted);
  cursor: pointer;
}
.pc-link:hover:not(:disabled) { color: var(--wb-fg); text-decoration: underline; }
.pc-link:disabled { opacity: 0.5; cursor: not-allowed; }
.pc-link.pc-danger { color: #dc2626; }
.pc-link.pc-danger:hover:not(:disabled) { color: #b91c1c; }

/* ===== 骨架屏（加载态）===== */
.pc-skel {
  height: 14px;
  border-radius: 4px;
  background: var(--wb-hover);
}
.pc-skel.skel-long { width: 100%; }
.pc-skel.skel-mid { width: 80%; }
.pc-skel.skel-short { width: 60%; }
.pc-skel-url {
  height: 38px;
  border-radius: 8px;
  background: var(--wb-hover);
  border: 1px solid #eaeaea;
}

/* ===== 全局错误 ===== */
.publish-error {
  color: #dc2626;
  font-size: 12px;
  margin: 10px 0 0;
  padding: 8px 12px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
}
.publish-warnings {
  margin: 10px 0 0;
  padding: 8px 12px 8px 30px;
  font-size: 12px;
  line-height: 1.6;
  color: #a16207;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 6px;
}
</style>
