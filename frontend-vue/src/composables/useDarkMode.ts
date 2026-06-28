import { watch } from 'vue'
import { useSettingsStore } from '@/stores/settings'

export function useDarkMode() {
  const settingsStore = useSettingsStore()

  // Apply theme on init
  settingsStore.applyTheme()

  // Watch for changes
  watch(() => settingsStore.darkMode, () => {
    settingsStore.applyTheme()
  })
}
