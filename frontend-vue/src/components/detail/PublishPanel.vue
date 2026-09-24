<template>
  <div class="publish-panel">
    <div class="publish-head">
      <h3>一键发布</h3>
      <p class="publish-sub">
        把当前生成的静态产物发布为一个可分享的链接。链接与内容解耦——重新发布同一需求，URL 不变、版本号 +1。
      </p>
    </div>

    <!-- 还未发布 / 重新发布 -->
    <div class="publish-actions">
      <button class="btn-publish" :disabled="publishing || !reqId" @click="onPublish">
        {{ publishing ? '发布中…' : (result ? '重新发布（新版本）' : '发布此需求') }}
      </button>
      <span v-if="!reqId" class="hint">未加载需求</span>
    </div>

    <!-- 发布结果卡片 -->
    <div v-if="result" class="publish-card">
      <div class="pc-row">
        <span class="pc-label">发布链接</span>
        <a class="pc-url" :href="openUrl" target="_blank" rel="noopener">{{ openUrl }}</a>
        <button class="pc-copy" @click="copyUrl">复制</button>
      </div>
      <div class="pc-meta">
        <span>版本 v{{ result.version }}</span>
        <span>可见性 {{ result.visibility === 'unlisted' ? '不公开（noindex）' : result.visibility }}</span>
        <span class="verify" :class="verifyClass">复验：{{ verifyLabel }}</span>
      </div>
      <div class="pc-actions">
        <a class="pc-open" :href="openUrl" target="_blank" rel="noopener">打开站点 ↗</a>
        <button class="pc-unpublish" :disabled="unpublishing" @click="onUnpublish">
          {{ unpublishing ? '取消中…' : '取消发布' }}
        </button>
      </div>
    </div>

    <p v-if="error" class="publish-error">{{ error }}</p>

    <p class="publish-note">
      开发期预览：未配置独立 apex 域名时，用 nip.io 通配 DNS 直接访问
      <code>http://{{ result?.slug }}.127.0.0.1.nip.io:{{ devPort }}/</code>
    </p>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRequirementStore } from '@/stores/requirement'
import { useToast } from '@/composables/useToast'
import { useApi } from '@/composables/useApi'

interface PublishResult {
  slug: string
  version: number
  visibility: string
  verify_status: string
  current_hash: string
  published_host: string | null
  url: string | null
}

const store = useRequirementStore()
const { show } = useToast()
const { api } = useApi()

const DEV_PORT = import.meta.env.VITE_PUBLISH_DEV_PORT || '5001'

const reqId = computed(() => store.currentRequirement?.id ?? null)
const publishing = ref(false)
const unpublishing = ref(false)
const result = ref<PublishResult | null>(null)
const error = ref('')

const verifyLabel = computed(() => {
  const m: Record<string, string> = {
    ok: '通过',
    degraded: '降级（上线有问题）',
    unverified: '未验证（未配 apex）',
    pending: '验证中',
  }
  return result.value ? (m[result.value.verify_status] || result.value.verify_status) : ''
})
const verifyClass = computed(() => `v-${result.value?.verify_status ?? 'pending'}`)

const openUrl = computed(() => {
  if (!result.value) return '#'
  // 生产：后端返回 https url；开发：用 nip.io 指向本机 backend 端口
  return result.value.url || `http://${result.value.slug}.127.0.0.1.nip.io:${DEV_PORT}/`
})

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
    show('发布成功', 'success')
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
  try {
    await navigator.clipboard.writeText(openUrl.value)
    show('链接已复制', 'success')
  } catch {
    error.value = '复制失败，请手动复制'
  }
}
</script>

<style scoped>
.publish-panel {
  padding: 24px;
  max-width: 720px;
  margin: 0 auto;
  color: var(--dark-fg);
  font-family: var(--font-body);
}
.publish-head h3 {
  font-size: 18px;
  margin: 0 0 6px;
}
.publish-sub {
  font-size: 13px;
  color: var(--dark-muted);
  line-height: 1.6;
  margin: 0 0 20px;
}
.publish-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}
.btn-publish {
  padding: 10px 22px;
  border-radius: 10px;
  border: none;
  background: var(--accent);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn-publish:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.hint {
  font-size: 12px;
  color: var(--dark-muted);
}
.publish-card {
  border: 1px solid var(--dark-border);
  border-radius: 12px;
  padding: 16px;
  background: var(--dark-surface);
}
.pc-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.pc-label {
  font-size: 12px;
  color: var(--dark-muted);
}
.pc-url {
  flex: 1;
  min-width: 0;
  color: var(--accent);
  font-size: 13px;
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-copy {
  border: 1px solid var(--dark-border);
  background: none;
  color: var(--dark-fg);
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}
.pc-meta {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  margin: 12px 0;
  font-size: 12px;
  color: var(--dark-muted);
}
.verify.v-ok { color: #34d399; }
.verify.v-degraded { color: #f59e0b; }
.verify.v-unverified, .verify.v-pending { color: var(--dark-muted); }
.pc-actions {
  display: flex;
  gap: 16px;
  align-items: center;
}
.pc-open {
  color: var(--accent);
  font-size: 13px;
  text-decoration: none;
}
.pc-unpublish {
  margin-left: auto;
  border: 1px solid var(--dark-border);
  background: none;
  color: #f87171;
  border-radius: 6px;
  padding: 6px 14px;
  font-size: 12px;
  cursor: pointer;
}
.pc-unpublish:disabled { opacity: 0.5; cursor: not-allowed; }
.publish-error {
  color: #f87171;
  font-size: 13px;
  margin-top: 12px;
}
.publish-note {
  font-size: 12px;
  color: var(--dark-muted);
  margin-top: 20px;
  line-height: 1.6;
}
.publish-note code {
  background: var(--dark-surface);
  padding: 2px 6px;
  border-radius: 4px;
}
</style>
