import { token, clearAuthStorage } from '../composables/useAuth'
import router from '../router'

export async function api(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers)
  if (token.value) {
    headers.set('Authorization', `Bearer ${token.value}`)
  }
  const res = await fetch(path, { ...options, headers })
  if (res.status === 401) {
    clearAuthStorage()
    router.push('/login')
    throw new Error('Unauthorized')
  }
  return res
}
