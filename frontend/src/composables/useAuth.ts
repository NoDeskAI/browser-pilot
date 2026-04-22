import { ref, readonly, computed } from 'vue'

export interface AuthUser {
  id: string
  email: string
  name: string
  role: string
  tenantId: string
  createdAt?: string
}

const _stored = localStorage.getItem('auth_token')
const _rememberMe = ref(!!_stored)
const _token = ref<string | null>(_stored || sessionStorage.getItem('auth_token'))
const _user = ref<AuthUser | null>(null)

export const token = readonly(_token)

export function clearAuthStorage() {
  localStorage.removeItem('auth_token')
  sessionStorage.removeItem('auth_token')
}

export function useAuth() {
  const isAuthenticated = computed(() => !!_token.value)

  function setAuth(accessToken: string, user: AuthUser, rememberMe = true) {
    _token.value = accessToken
    _user.value = user
    _rememberMe.value = rememberMe
    if (rememberMe) {
      localStorage.setItem('auth_token', accessToken)
      sessionStorage.removeItem('auth_token')
    } else {
      sessionStorage.setItem('auth_token', accessToken)
      localStorage.removeItem('auth_token')
    }
  }

  function logout() {
    _token.value = null
    _user.value = null
    clearAuthStorage()
  }

  async function fetchMe(): Promise<boolean> {
    if (!_token.value) return false
    try {
      const res = await fetch('/api/auth/me', {
        headers: { Authorization: `Bearer ${_token.value}` },
      })
      if (!res.ok) {
        logout()
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
  }
}
