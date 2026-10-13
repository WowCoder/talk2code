import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { getAdminToken } from '@/composables/useAdmin'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: () => import('@/views/LoginView.vue'),
      meta: { requiresAuth: false },
    },
    {
      path: '/',
      name: 'Home',
      component: () => import('@/views/HomeView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 地址栏只放 slug：自增 id 会泄露平台累计需求数，还能被顺序枚举。
      // 老的 /detail/123 仍能打开（详情页加载后 replace 成 slug 版）。
      path: '/detail/:slug',
      name: 'Detail',
      component: () => import('@/views/DetailView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 创意市集：免登录可逛。匿名访客能看见作品、能点赞（第 3 次互动才引导注册），
      // 这是「陌生人 → 注册」转化路径的入口。
      path: '/market',
      name: 'Market',
      component: () => import('@/views/MarketView.vue'),
      meta: { requiresAuth: false },
    },
    {
      path: '/market/:slug',
      name: 'MarketDetail',
      component: () => import('@/views/MarketDetailView.vue'),
      meta: { requiresAuth: false },
    },
    {
      path: '/market/u/:id',
      name: 'MarketAuthor',
      component: () => import('@/views/MarketAuthorView.vue'),
      meta: { requiresAuth: false },
    },
    {
      path: '/history',
      name: 'History',
      component: () => import('@/views/HistoryView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/settings',
      name: 'Settings',
      component: () => import('@/views/SettingsView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 运营后台：入口不进主导航（普通用户不感知）。token 存 sessionStorage，
      // 有效期 2h，过期由 useAdmin 统一踢回登录页。默认落点是总览 —— 先看数
      // 字再处理待办，比一进来就掉进审批队列更顺。
      path: '/admin',
      redirect: '/admin/metrics',
    },
    {
      path: '/admin/login',
      name: 'AdminLogin',
      component: () => import('@/views/admin/AdminLoginView.vue'),
      meta: { requiresAuth: false, admin: true },
    },
    {
      path: '/admin/invites',
      name: 'AdminInvites',
      component: () => import('@/views/admin/AdminInvitesView.vue'),
      meta: { requiresAuth: false, requiresAdmin: true },
    },
    {
      path: '/admin/metrics',
      name: 'AdminMetrics',
      component: () => import('@/views/admin/AdminMetricsView.vue'),
      meta: { requiresAuth: false, requiresAdmin: true },
    },
    {
      path: '/admin/traces',
      name: 'AdminTraces',
      component: () => import('@/views/admin/AdminTracesView.vue'),
      meta: { requiresAuth: false, requiresAdmin: true },
    },
    {
      // 单需求的时间线 / 轮次 / 事件明细
      path: '/admin/traces/:id',
      name: 'AdminTraceDetail',
      component: () => import('@/views/admin/AdminTraceDetailView.vue'),
      meta: { requiresAuth: false, requiresAdmin: true },
    },
    {
      // 评测集：每次离线跑评测留一个运行目录，这里看过程与结果。
      // 与需求轨迹是两条数据源（本地 SQLite vs 运营库），但共用同一套渲染。
      path: '/admin/evals',
      name: 'AdminEvals',
      component: () => import('@/views/admin/AdminEvalsView.vue'),
      meta: { requiresAuth: false, requiresAdmin: true },
    },
    {
      path: '/admin/evals/:runId',
      name: 'AdminEvalDetail',
      component: () => import('@/views/admin/AdminEvalDetailView.vue'),
      meta: { requiresAuth: false, requiresAdmin: true },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

// Navigation guard
//
// 应用不再等登录态探测完成才挂载（见 main.ts），所以这里要自己决定「这次判定
// 是否真的需要登录态」：需要才 await authStore.authReady，不需要就直接放行 ——
// 免登录页面（市集、后台）因此可以立刻渲染，不为一个后台探测付首屏时间。
router.beforeEach(async (to, _from, next) => {
  // 后台路由用独立的 sessionStorage 登录态，与前台 cookie 无关：既不 await 前台探测，
  // 也不会被前台登录态影响。
  if (to.meta.requiresAdmin) {
    if (getAdminToken()) next()
    else next({ name: 'AdminLogin' })
    return
  }

  const authStore = useAuthStore()

  // 需要等的三种情况：
  //   · requiresAuth 未显式声明为 false（首页、详情、历史、设置、通配重定向）
  //   · 登录页 —— 已登录时要跳回首页，判定依赖登录态
  // meta.requiresAuth === false 的市集各页不在此列。
  if (to.meta.requiresAuth !== false || to.name === 'Login') {
    await authStore.authReady
  }

  if (!authStore.isAuthenticated && to.path === '/') {
    // 游客的默认落点是市集：先让人看见别人做出来的东西，比先甩一个登录框
    // 更能说明这产品能干什么。（/history、/settings 仍走下面的登录门禁）
    next({ name: 'Market' })
  } else if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    next({ name: 'Login' })
  } else if (to.name === 'Login' && authStore.isAuthenticated) {
    next({ name: 'Home' })
  } else {
    next()
  }
})

export default router
