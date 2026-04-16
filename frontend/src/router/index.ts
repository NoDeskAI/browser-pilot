import { createRouter, createWebHistory } from 'vue-router'
import MainView from '../views/MainView.vue'
import { token } from '../composables/useAuth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: MainView, meta: { requiresAuth: true } },
    { path: '/settings', component: () => import('../views/SettingsView.vue'), meta: { requiresAuth: true } },
    { path: '/users', component: () => import('../views/UsersView.vue'), meta: { requiresAuth: true } },
    { path: '/login', component: () => import('../views/LoginView.vue') },
    { path: '/setup', component: () => import('../views/SetupView.vue') },
  ],
})

let _siteInfoCache: { setupComplete: boolean } | null = null

async function getSiteInfo(): Promise<{ setupComplete: boolean }> {
  if (_siteInfoCache) return _siteInfoCache
  try {
    const res = await fetch('/api/site-info')
    const data = await res.json()
    _siteInfoCache = { setupComplete: !!data.setupComplete }
    return _siteInfoCache
  } catch {
    return { setupComplete: true }
  }
}

router.beforeEach(async (to) => {
  const info = await getSiteInfo()

  if (!info.setupComplete && to.path !== '/setup') {
    return '/setup'
  }

  if (info.setupComplete && to.path === '/setup') {
    return '/login'
  }

  if (to.meta.requiresAuth && !token.value) {
    return '/login'
  }

  if (to.path === '/login' && token.value) {
    return '/'
  }
})

export function invalidateSiteInfoCache() {
  _siteInfoCache = null
}

export default router
