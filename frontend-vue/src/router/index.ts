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
      path: '/detail/:id',
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
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

// Navigation guard
router.beforeEach((to, _from, next) => {
  const authStore = useAuthStore()

  // 后台路由：独立的 sessionStorage 登录态，与前台 cookie 互不干扰
  if (to.meta.requiresAdmin && !getAdminToken()) {
    next({ name: 'AdminLogin' })
    return
  }

  // 登录态已在 main.ts 中通过 initAuth() 恢复
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
