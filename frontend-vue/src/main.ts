import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { useAuthStore } from '@/stores/auth'
import './assets/styles/variables.css'
import './assets/styles/base.css'
import './assets/styles/transitions.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

// 挂载前先恢复登录态（httpOnly cookie 需异步请求后端确认），
// 保证路由守卫第一次导航时 isAuthenticated 已正确。
// 用 async 函数包裹，避免 top-level await（es2020 目标不支持）。
async function bootstrap() {
  const authStore = useAuthStore(pinia)
  await authStore.initAuth()
  app.use(router)
  app.mount('#app')
}
bootstrap()
