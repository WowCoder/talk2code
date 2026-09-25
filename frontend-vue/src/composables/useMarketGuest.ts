// 市集游客引导：只在「想做点什么却做不了」的那一刻出现，不主动打扰。
// 抽出来是给市集列表页与详情页共用 —— 两处各写一份 localStorage 计数
// 会导致阈值不一致，用户在列表点两次、详情点两次就永远等不到引导。
const GUEST_ACTIONS_KEY = 'market_guest_actions'
const NUDGE_THRESHOLD = 3 // 第一次就弹是打扰；第三次说明他真的想参与

export function useMarketGuest() {
  function bumpGuestAction(authed: boolean): boolean {
    if (authed) return false
    const n = Number(localStorage.getItem(GUEST_ACTIONS_KEY) || '0') + 1
    localStorage.setItem(GUEST_ACTIONS_KEY, String(n))
    return n >= NUDGE_THRESHOLD
  }

  function clearGuestActions(): void {
    localStorage.removeItem(GUEST_ACTIONS_KEY)
  }

  return { bumpGuestAction, clearGuestActions, NUDGE_THRESHOLD }
}
