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
        <div class="pc-foot-hint">
          <strong>为什么要有这个加载态</strong>
          <p>进入 TAB 时会向后端查询当前需求是否有已发布记录：404 → 未发布；200 → 切换为「已发布」状态。整个查询耗时 &lt; 100ms，骨架屏只在弱网下短暂可见。</p>
        </div>
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
        <button class="btn-publish btn-secondary" :disabled="publishing || unpublishing" @click="onPublish">
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
        <div class="pc-actions">
          <button class="pc-link" :disabled="publishing || unpublishing" @click="onPublish">
            {{ publishing ? '发布中…' : '重新发布（新版本）' }}
          </button>
          <button class="pc-link pc-danger" :disabled="unpublishing" @click="onUnpublish">
            {{ unpublishing ? '取消中…' : '取消发布' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 未发布 -->
    <div v-else class="publish-card">
      <div class="pc-head">
        <h3>发布管理</h3>
        <span class="pc-status">
          <span class="pc-dot"></span>未发布
        </span>
      </div>
      <div class="pc-body">
        <p class="pc-intro">
          把当前生成的静态产物发布为一个可分享的链接。链接与内容解耦——重新发布同一需求，URL 不变、版本号 +1。
        </p>
        <div class="pc-meta-card">
          <span class="pc-dot ready"></span>
          <span class="pc-meta-ready">代码已就绪，可发布</span>
          <span class="pc-meta-sub">index.html · 3 个资源</span>
        </div>
        <button class="btn-publish" :disabled="publishing || !reqId" @click="onPublish">
          {{ publishing ? '发布中…' : '发布此需求' }}
        </button>
        <p class="pc-tip">发布后会得到一个可分享的链接，访问者无需登录即可打开。</p>
        <div class="pc-foot-hint">
          <strong>发布后你会得到</strong>
          <p>· 一个 &lt;slug&gt;.&lt;apex&gt; 形式的可分享链接</p>
          <p>· 每次重新发布：URL 不变、版本号 +1</p>
          <p>· 取消发布会移除访问入口（产物保留）</p>
        </div>
      </div>
    </div>

    <!-- 全局动作错误（发布/取消失败） -->
    <p v-if="error" class="publish-error">{{ error }}</p>
    <!-- 首屏拉取错误（非 404 的真错误；404 走「未发布」分支） -->
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

interface PublishResult {
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
}

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

// 进入 TAB 时拉一次发布状态：解决「刷新后只剩一个发布按钮」的持久化问题。
// 404 是正常态（该需求从未发布），不是错误。
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
    result.value = data
  } catch (e) {
    const msg = (e as Error).message || ''
    // 404 = 未发布（正常态）；其它才视为拉取错误
    if (/404|尚未发布/.test(msg)) {
      result.value = null
    } else {
      loadError.value = msg
      result.value = null
    }
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
    } else {
      show('已保存产物，但当前没有可访问的发布域名', 'error')
    }
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    publishing.value = false
  }
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
/* ===== 容器：与 SpecPanel/TaskPanel 同构 —— 卡片铺满右栏，不再 max-width 居中 ===== */
.publish-panel {
  padding: 12px;
  color: var(--fg);
  font-family: var(--font-body);
}

.publish-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}

/* ===== 标题栏：与 spec-header / task-header 同模式 ===== */
.pc-head {
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--fg);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pc-head h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--fg);
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
  background: #f5f5f5;
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
  background: #f5f5f5;
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

/* 介绍 / 提示段落 */
.pc-intro,
.pc-loading-hint,
.pc-tip {
  margin: 0;
  font-size: 13px;
  color: var(--fg);
  line-height: 1.6;
}
.pc-loading-hint { color: var(--muted); }
.pc-tip { color: var(--muted); }

/* ===== 元信息行（版本 · 时间 · 访问数）===== */
.pc-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--muted);
}
.pc-meta span { white-space: nowrap; }
.pc-sep {
  width: 1px !important;
  height: 12px;
  background: var(--border);
  display: inline-block;
}

/* ===== 元信息卡（绿色「代码已就绪」）===== */
.pc-meta-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  background: #fafafa;
  border: 1px solid var(--border);
  font-size: 12px;
}
.pc-meta-ready {
  color: #16a34a;
  font-weight: 500;
}
.pc-meta-sub {
  color: var(--muted);
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 12px;
}

/* ===== URL 区 ===== */
.pc-url-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.pc-url-box {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: #fafafa;
  border: 1px solid var(--border);
}
.pc-url-box.disabled {
  background: #fafafa;
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
  color: var(--muted);
  font-size: 12px;
}
.pc-copy {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--fg);
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
}
.pc-copy:hover { background: #fafafa; }
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
  background: #f5f5f5;
  color: var(--muted);
  border: 1px solid var(--border);
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
  color: var(--muted);
  cursor: pointer;
}
.pc-link:hover:not(:disabled) { color: var(--fg); text-decoration: underline; }
.pc-link:disabled { opacity: 0.5; cursor: not-allowed; }
.pc-link.pc-danger { color: #dc2626; }
.pc-link.pc-danger:hover:not(:disabled) { color: #b91c1c; }

/* ===== 骨架屏（加载态）===== */
.pc-skel {
  height: 14px;
  border-radius: 4px;
  background: #f0f0f0;
}
.pc-skel.skel-long { width: 100%; }
.pc-skel.skel-mid { width: 80%; }
.pc-skel.skel-short { width: 60%; }
.pc-skel-url {
  height: 38px;
  border-radius: 8px;
  background: #f5f5f5;
  border: 1px solid #eaeaea;
}

/* ===== 底部说明框（解释性 footer，复用 .pc-meta-card 的视觉风格）===== */
.pc-foot-hint {
  padding: 10px 12px;
  border-radius: 6px;
  background: #fafafa;
  border: 1px solid var(--border);
  font-size: 12px;
}
.pc-foot-hint strong {
  display: block;
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 6px;
}
.pc-foot-hint p {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--fg);
  line-height: 1.6;
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
