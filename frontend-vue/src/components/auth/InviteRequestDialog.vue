<template>
  <Teleport to="body">
    <Transition name="dialog">
      <div v-if="show" class="dialog-mask" @click.self="emit('close')">
        <div class="dialog-card" role="dialog" aria-modal="true" aria-label="申请邀请码">
          <h3 class="dialog-title">申请邀请码</h3>
          <p class="dialog-sub">提交后由管理员审批，通过后邀请码会发送到你的邮箱。</p>

          <template v-if="!submitted">
            <div class="form-group">
              <label class="sr-only" for="invite-email">邮箱</label>
              <input
                id="invite-email"
                v-model="email"
                type="email"
                class="input-field"
                placeholder="邮箱（用于接收邀请码）"
                required
              />
            </div>
            <div class="form-group">
              <label class="sr-only" for="invite-phone">手机号</label>
              <input
                id="invite-phone"
                v-model="phone"
                type="tel"
                class="input-field"
                placeholder="手机号（仅作留存，不会短信发送）"
                required
              />
            </div>
            <div class="form-group">
              <label class="sr-only" for="invite-note">用途说明</label>
              <textarea
                id="invite-note"
                v-model="note"
                class="input-field note-field"
                rows="3"
                maxlength="500"
                placeholder="简单说说你想用 Talk2Code 做什么（选填）"
              ></textarea>
            </div>
            <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
            <div class="dialog-actions">
              <button type="button" class="btn-ghost" @click="emit('close')">取消</button>
              <button
                type="button"
                class="btn-primary dialog-submit"
                :disabled="loading"
                @click="submit"
              >{{ loading ? '提交中…' : '提交申请' }}</button>
            </div>
          </template>

          <template v-else>
            <p class="dialog-done">{{ doneMessage }}</p>
            <div class="dialog-actions">
              <button type="button" class="btn-primary dialog-submit" @click="emit('close')">好的</button>
            </div>
          </template>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ close: [] }>()

const email = ref('')
const phone = ref('')
const note = ref('')
const loading = ref(false)
const errorMsg = ref('')
const submitted = ref(false)
const doneMessage = ref('')

// 每次打开重置提交态（保留已填字段，方便修正后重提）
watch(() => props.show, (v) => {
  if (v) {
    submitted.value = false
    errorMsg.value = ''
  }
})

async function submit() {
  errorMsg.value = ''
  if (!email.value.trim() || !phone.value.trim()) {
    errorMsg.value = '请填写邮箱和手机号'
    return
  }

  loading.value = true
  try {
    const resp = await fetch('/api/invite/requests', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: email.value.trim(),
        phone: phone.value.trim(),
        note: note.value.trim(),
      }),
    })
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      throw new Error(data.error || '提交失败，请稍后重试')
    }
    // 幂等场景（24h 内重复申请）与首次提交共用同一文案，不区分展示
    doneMessage.value = data.message || '申请已提交，审批通过后邀请码将发送到你的邮箱'
    submitted.value = true
  } catch (err: any) {
    errorMsg.value = err.message || '提交失败，请稍后重试'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.dialog-mask {
  position: fixed;
  inset: 0;
  z-index: 300;
  display: flex;
  align-items: center;
  justify-content: center;
  background: oklch(0% 0 0 / 40%);
  padding: 24px;
}

.dialog-card {
  width: 420px;
  max-width: 100%;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 24px;
}

.dialog-title {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 700;
  color: var(--fg);
  margin-bottom: 6px;
}

.dialog-sub {
  font-size: 13px;
  color: var(--muted);
  margin-bottom: 16px;
  line-height: 1.5;
}

.form-group {
  margin-bottom: 12px;
}

.note-field {
  resize: vertical;
  min-height: 64px;
}

.form-error {
  font-size: 12px;
  color: oklch(50% 0.15 20);
  margin-bottom: 10px;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 6px;
}

.btn-ghost {
  padding: 9px 18px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: transparent;
  color: var(--muted);
  font-size: 14px;
  font-family: var(--font-body);
  cursor: pointer;
}

.btn-ghost:hover {
  color: var(--fg);
  border-color: var(--border);
}

.dialog-submit {
  padding: 9px 18px;
  font-size: 14px;
}

.dialog-done {
  font-size: 14px;
  color: var(--fg);
  line-height: 1.6;
  margin-bottom: 12px;
}

.dialog-enter-active,
.dialog-leave-active { transition: opacity 0.18s ease; }
.dialog-enter-active .dialog-card,
.dialog-leave-active .dialog-card { transition: transform 0.18s ease; }
.dialog-enter-from,
.dialog-leave-to { opacity: 0; }
.dialog-enter-from .dialog-card,
.dialog-leave-to .dialog-card { transform: translateY(8px) scale(0.98); }

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
