import { ref, readonly, computed } from 'vue'

export interface AuthUser {
  id: string
  email: string
  name: string
  role: string
  tenantId: string
  createdAt?: string
}

localStorage.removeItem('auth_token')
localStorage.removeItem('saved_email')

const _token = ref<string | null>(sessionStorage.getItem('auth_token'))
const _user = ref<AuthUser | null>(null)
let _refreshPromise: Promise<boolean> | null = null

export const token = readonly(_token)

export function clearAuthStorage() {
  localStorage.removeItem('auth_token')
  localStorage.removeItem('saved_email')
  sessionStorage.removeItem('auth_token')
}

function setAuthState(accessToken: string, user: AuthUser) {
  _token.value = accessToken
  _user.value = user
  sessionStorage.setItem('auth_token', accessToken)
}

async function refreshAuthImpl(): Promise<boolean> {
  try {
    const res = await fetch('/api/auth/refresh', {
      method: 'POST',
      credentials: 'same-origin',
    })
    if (!res.ok) {
      clearAuthStorage()
      _token.value = null
      _user.value = null
      return false
    }
    const data = await res.json()
    setAuthState(data.access_token, data.user)
    return true
  } catch {
    return false
  }
}

export function refreshAuth(): Promise<boolean> {
  if (!_refreshPromise) {
    _refreshPromise = refreshAuthImpl().finally(() => {
      _refreshPromise = null
    })
  }
  return _refreshPromise
}

export function useAuth() {
  const isAuthenticated = computed(() => !!_token.value)

  function setAuth(accessToken: string, user: AuthUser) {
    setAuthState(accessToken, user)
  }

  async function logout() {
    _token.value = null
    _user.value = null
    clearAuthStorage()
    try {
      await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'same-origin',
      })
    } catch {
      // local auth state is already cleared
    }
  }

  async function fetchMe(): Promise<boolean> {
    if (!_token.value && !(await refreshAuth())) return false
    try {
      let res = await fetch('/api/auth/me', {
        headers: { Authorization: `Bearer ${_token.value}` },
      })
      if (res.status === 401 && await refreshAuth()) {
        res = await fetch('/api/auth/me', {
          headers: { Authorization: `Bearer ${_token.value}` },
        })
      }
      if (!res.ok) {
        _token.value = null
        _user.value = null
        clearAuthStorage()
        return false
      }
      _user.value = await res.json()
      return true
    } catch {
      return false
    }
  }

  return {
    token: readonly(_token),
    user: readonly(_user),
    isAuthenticated,
    setAuth,
    logout,
    fetchMe,
    refreshAuth,
  }
}
