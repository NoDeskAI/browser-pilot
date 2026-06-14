import { createRouter, createWebHistory } from 'vue-router'
import { token, refreshAuth } from '../composables/useAuth'
import { eePublicRoutePrefixes, eeRoutes } from '@ee/routes'

const publicRoutePrefixes = __EE__ ? eePublicRoutePrefixes : []

export function isPublicShellRoute(path: string): boolean {
  return publicRoutePrefixes.some(prefix => path === prefix || path.startsWith(`${prefix}/`))
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('../views/DashboardView.vue'), meta: { requiresAuth: true } },
    { path: '/s/:id', component: () => import('../views/MainView.vue'), meta: { requiresAuth: true } },
    { path: '/files', component: () => import('../views/FilesView.vue'), meta: { requiresAuth: true } },
    { path: '/agent-devices', component: () => import('../views/AgentDevicesView.vue'), meta: { requiresAuth: true } },
    { path: '/settings/:section?', component: () => import('../views/SettingsView.vue'), meta: { requiresAuth: true } },
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
    { path: '/register', component: () => import('../views/RegisterView.vue') },
    { path: '/setup', component: () => import('../views/SetupView.vue') },
    ...(__EE__ ? eeRoutes : []),
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

let _siteInfoCache: { setupComplete: boolean; bootstrapBlocked?: boolean } | null = null

async function getSiteInfo(): Promise<{ setupComplete: boolean; bootstrapBlocked?: boolean }> {
  if (_siteInfoCache) return _siteInfoCache
  try {
    const res = await fetch('/api/site-info')
    const data = await res.json()
    if (!res.ok) {
      return { setupComplete: false, bootstrapBlocked: true }
    }
    _siteInfoCache = { setupComplete: !!data.setupComplete }
    return _siteInfoCache
  } catch {
    return { setupComplete: false, bootstrapBlocked: true }
  }
}

router.beforeEach(async (to) => {
  const info = await getSiteInfo()
  if (info.bootstrapBlocked) {
    return true
  }

  if (!info.setupComplete && to.path !== '/setup' && !isPublicShellRoute(to.path)) {
    return '/setup'
  }

  if (info.setupComplete && to.path === '/setup') {
    return '/login'
  }

  if (to.meta.requiresAuth && !token.value && !(await refreshAuth())) {
    return '/login'
  }

  if ((to.path === '/login' || to.path === '/register') && (!token.value ? await refreshAuth() : true)) {
    return '/'
  }
})

export function invalidateSiteInfoCache() {
  _siteInfoCache = null
}

export default router
