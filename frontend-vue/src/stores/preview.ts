import { defineStore } from 'pinia'
import { ref } from 'vue'

export type PreviewStatusType = 'passed' | 'failed' | 'unavailable' | null

export const usePreviewStore = defineStore('preview', () => {
  const status = ref<PreviewStatusType>(null)
  const tooltip = ref('')
  const errors = ref<string[]>([])

  function updatePreviewStatus(
    s: PreviewStatusType,
    errs: string[],
    tip: string
  ) {
    status.value = s
    errors.value = errs
    tooltip.value = tip || (errs.length > 0 ? errs.join('; ') : '')
  }

  function reset() {
    status.value = null
    tooltip.value = ''
    errors.value = []
  }

  return { status, tooltip, errors, updatePreviewStatus, reset }
})
