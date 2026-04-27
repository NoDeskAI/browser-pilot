import { createRouter, createWebHistory } from 'vue-router'
import { token } from '../composables/useAuth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('../views/DashboardView.vue'), meta: { requiresAuth: true } },
    { path: '/s/:id', component: () => import('../views/MainView.vue'), meta: { requiresAuth: true } },
    { path: '/settings', component: () => import('../views/SettingsView.vue'), meta: { requiresAuth: true } },
    { path: '/users', component: () => import('../views/UsersView.vue'), meta: { requiresAuth: true } },
    { path: '/account', component: () => import('../views/AccountView.vue'), meta: { requiresAuth: true } },
    {
      path: '/docs',
      component: () => import('../views/DocsView.vue'),
      meta: { requiresAuth: true },
      redirect: '/docs/cli',
      children: [
        { path: 'cli', component: () => import('../views/CliDocPage.vue'), props: { mode: 'manual' } },
        { path: 'cli-agent', component: () => import('../views/CliDocPage.vue'), props: { mode: 'agent' } },
        { path: 'api-token', component: () => import('../views/ApiTokenDocView.vue') },
      ],
    },
    { path: '/login', component: () => import('../views/LoginView.vue') },
    { path: '/setup', component: () => import('../views/SetupView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
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

if (__EE__) {
  import('@ee/routes').then(m => m.eeRoutes.forEach((r: any) => router.addRoute(r)))
}

export default router
