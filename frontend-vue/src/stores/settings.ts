import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useSettingsStore = defineStore('settings', () => {
  const darkMode = ref(localStorage.getItem('darkMode') === 'true')
  const codeFontSize = ref(localStorage.getItem('codeFontSize') || '14px')
  const autoSave = ref(localStorage.getItem('autoSave') !== 'false') // default true

  function toggleDarkMode() {
    darkMode.value = !darkMode.value
    localStorage.setItem('darkMode', String(darkMode.value))
    applyTheme()
  }

  function setCodeFontSize(size: string) {
    codeFontSize.value = size
    localStorage.setItem('codeFontSize', size)
  }

  function toggleAutoSave() {
    autoSave.value = !autoSave.value
    localStorage.setItem('autoSave', String(autoSave.value))
  }

  function applyTheme() {
    if (darkMode.value) {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  }

  return {
    darkMode,
    codeFontSize,
    autoSave,
    toggleDarkMode,
    setCodeFontSize,
    toggleAutoSave,
    applyTheme,
  }
})
