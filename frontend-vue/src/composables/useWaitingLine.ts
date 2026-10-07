import { ref, watch, onUnmounted, type Ref } from 'vue'
import { WAITING_LINES, createLineRotator } from '@/utils/waitingLines'

/**
 * 等待期的轮换文案：按当前阶段取一句，每隔 rotateMs 换一句。
 *
 * 两个细节决定了它是不是真的有用：
 *   - 阶段一变立刻换一句（等到下一个轮换周期才变，会出现「已经在验收了，
 *     还在说编码的俏皮话」这种错位的幽默）；
 *   - 进页面时任务已经在跑（active 一开始就是 true）也要立刻出一句，
 *     不能等第一次状态变化——那种情况下永远不会有变化，界面会空一行。
 */
export function useWaitingLine(active: Ref<boolean>, stage: Ref<string>) {
  const line = ref('')
  const rotator = createLineRotator(WAITING_LINES)
  let timer: ReturnType<typeof setInterval> | null = null

  function refresh() {
    line.value = rotator.next()
  }

  function enterStage() {
    rotator.setStage(stage.value)
    refresh()
  }

  function start() {
    stop()
    enterStage()
    timer = setInterval(refresh, WAITING_LINES.rotateMs)
  }

  function stop() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  watch(active, (on) => {
    if (on) {
      start()
    } else {
      stop()
      line.value = ''
    }
  })

  watch(stage, () => {
    if (!active.value) return
    enterStage()
  })

  onUnmounted(stop)

  // 首帧就处于生成中（刷新页面 / 从列表进入正在跑的需求）：watch 不会触发，
  // 必须在这里直接起一次，否则整段等待期都不会出现文案。
  if (active.value) start()

  return { line }
}
