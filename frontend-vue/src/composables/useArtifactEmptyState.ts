import { computed } from 'vue'
import { useRequirementStore } from '@/stores/requirement'

/**
 * 产物空态：代码 TAB 与预览 TAB 在没有产物时都是一片空白，用户分不清
 * 「还在生成」和「这次失败了」——需求 162 失败后两个面板全空，用户只能靠猜。
 * 这里按需求状态给出唯一一套文案，两个面板共用，避免两处各写一份、
 * 改的时候漏掉一处。
 */
export function useArtifactEmptyState() {
  const store = useRequirementStore()

  // 生成过程中详情接口的 code_files 还是空的，SSE 增量只写入 codeFiles 映射
  // ——两处任一非空即视为有产物，否则代码/预览 TAB 在整个生成期间都是空态
  const hasFiles = computed(
    () =>
      (store.currentRequirement?.code_files?.length ?? 0) > 0 ||
      Object.keys(store.codeFiles).length > 0,
  )
  const status = computed(() => store.currentRequirement?.status ?? null)
  // interrupted 仍可续跑，观感上属于「还没做完」，不算终态
  const inProgress = computed(() =>
    ['pending', 'planning', 'processing', 'interrupted'].includes(status.value ?? '')
  )

  const title = computed(() => {
    if (inProgress.value) return '代码生成中…'
    if (status.value === 'failed') return '这次生成没能产出代码'
    return '尚未生成代码'
  })

  const hint = computed(() => {
    if (inProgress.value) return '生成完成后这里会显示结果。'
    if (status.value === 'failed') return '可以点击页面顶部的「重新生成」再来一次。'
    return '在左侧对话里描述你要的东西，生成完成后这里会显示结果。'
  })

  return { hasFiles, inProgress, status, title, hint }
}
