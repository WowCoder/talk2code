<template>
  <router-view />
  <ToastContainer />
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import ToastContainer from '@/components/common/ToastContainer.vue'

const settingsStore = useSettingsStore()

onMounted(() => {
  // 登录态恢复已前移到 main.ts（initAuth），此处仅应用主题
  settingsStore.applyTheme()
})
</script>
