import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { useAuthStore } from '@/stores/auth'
// 自托管字体的 @font-face 声明（生成物，见 scripts/fetch-fonts.mjs）。
// 只有声明、不产生请求；真正加载哪几个字形分片由 unicode-range 决定。
import './assets/styles/fonts.css'
import './assets/styles/variables.css'
import './assets/styles/base.css'
import './assets/styles/transitions.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

// 挂载与登录态探测**并行**，不等探测结果。
//
// 原先的写法是 `await authStore.initAuth()` 之后再 mount，而 #app 在挂载前是空的
// —— 于是每个页面（包括免登录的市集）的首屏内容都被一个 /api/user/info 往返挡住，
// initAuth 内部还有 4 秒超时兜底，最坏情况就是 4 秒白屏。
//
// 现在：立刻挂载，路由守卫只在「判定确实依赖登录态」的路由（首页、详情、历史、
// 设置、登录页）上 await authStore.authReady；市集与后台路由直接渲染。
async function bootstrap() {
  const authStore = useAuthStore(pinia)
  const authSettled = authStore.initAuth().catch(() => {
    // initAuth 内部已兜底，这里只是防止未捕获的 rejection 冒到控制台
  })
  app.use(router)
  app.mount('#app')
  await authSettled
}
void bootstrap()
