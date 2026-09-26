<template>
  <Transition name="banner">
    <div v-if="authStore.isDemo" class="demo-banner">
      <span class="demo-dot"></span>
      <span class="demo-text">演示模式 · 只读：可以随便看，创建与修改在注册后解锁</span>
      <button class="demo-exit" @click="onExit">退出演示</button>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const router = useRouter()

async function onExit() {
  await authStore.logout()
  router.push({ name: 'Login' })
}
</script>

<style scoped>
/* 用文档流而不是 fixed：fixed 会浮在导航上方挡住 TAB。放在 App.vue 顶部
   作为普通块级元素，自然把导航和内容往下推，各视图均为文档流布局无 100vh 陷阱。 */
.demo-banner {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 8px 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  font-size: 13px;
  color: var(--fg);
}

.demo-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
  flex-shrink: 0;
}

.demo-text {
  color: var(--muted);
}

.demo-exit {
  padding: 4px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: transparent;
  color: var(--fg);
  font-size: 12px;
  font-family: var(--font-body);
  cursor: pointer;
  transition: all 0.15s;
}

.demo-exit:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.banner-enter-active,
.banner-leave-active { transition: all 0.2s ease; }
.banner-enter-from,
.banner-leave-to { opacity: 0; transform: translateY(-100%); }
</style>
