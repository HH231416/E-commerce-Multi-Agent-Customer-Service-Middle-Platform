import { createRouter, createWebHashHistory } from 'vue-router'
import { setUnauthorizedHandler } from './api/client'
import { useAuth } from './composables/useAuth'
import LoginView from './views/LoginView.vue'
import ChatView from './views/ChatView.vue'

// 用 hash 路由：构建产物以静态目录 /app 托管，无需后端做 history fallback。
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/chat', name: 'chat', component: ChatView, meta: { requiresIdentity: true } },
    { path: '/:pathMatch(.*)*', redirect: '/chat' },
  ],
})

router.beforeEach((to) => {
  const { state } = useAuth()
  if (to.meta.requiresIdentity && !state.userId) {
    return { name: 'login' }
  }
  if (to.name === 'login' && state.userId) {
    return { name: 'chat' }
  }
  return true
})

// 收到 401 时清理身份并回到登录页，避免用户卡在必然失败的页面
setUnauthorizedHandler(() => {
  const { signOut } = useAuth()
  signOut()
  if (router.currentRoute.value.name !== 'login') {
    void router.replace({ name: 'login', query: { reason: 'expired' } })
  }
})

export default router