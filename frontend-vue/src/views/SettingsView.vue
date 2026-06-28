<template>
  <div class="settings-page">
    <AppNav />
    <main class="settings-main">
      <SettingsSidebar v-model:activeSection="activeSection" />
      <div class="settings-content">
        <ProfileSection v-if="activeSection === 'profile'" />
        <AppearanceSection v-if="activeSection === 'appearance'" />
        <AccountSection v-if="activeSection === 'account'" />
        <AboutSection v-if="activeSection === 'about'" />
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import AppNav from '@/components/layout/AppNav.vue'
import SettingsSidebar from '@/components/settings/SettingsSidebar.vue'
import ProfileSection from '@/components/settings/ProfileSection.vue'
import AppearanceSection from '@/components/settings/AppearanceSection.vue'
import AccountSection from '@/components/settings/AccountSection.vue'
import AboutSection from '@/components/settings/AboutSection.vue'

const activeSection = ref('profile')
</script>

<style scoped>
.settings-page {
  min-height: 100vh;
  background: var(--bg);
}

.settings-main {
  max-width: 820px;
  margin: 0 auto;
  padding: 32px 24px;
  display: flex;
  gap: 32px;
}

.settings-content {
  flex: 1;
  min-width: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 28px;
}
</style>
