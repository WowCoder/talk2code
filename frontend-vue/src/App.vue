<template>
  <router-view />
  <ToastContainer />
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'
import ToastContainer from '@/components/common/ToastContainer.vue'

const authStore = useAuthStore()
const settingsStore = useSettingsStore()

onMounted(() => {
  authStore.initFromStorage()
  settingsStore.applyTheme()
})
</script>
