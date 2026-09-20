import { clearAuthSession, getAuthToken } from '../auth.js'

export function applyAuthToRequestConfig(config, token = getAuthToken()) {
  const nextConfig = { ...config, headers: { ...(config.headers || {}) } }
  // Keep the token used by this request so a stale response cannot clear a
  // newer login session created while the request was in flight.
  nextConfig._authToken = token || ''

  if (token) {
    nextConfig.headers.Authorization = `Bearer ${token}`
  }

  return nextConfig
}

export function handleAuthError(error) {
  // A few auth endpoints use 401 as a normal capability probe. Those
  // requests must be handled by their caller instead of logging the user out.
  if (error.config?.skipAuthRedirect) {
    return Promise.reject(error)
  }

  if (error.response?.status === 401) {
    const requestToken = error.config?._authToken || ''
    const currentToken = getAuthToken()
    if (requestToken && requestToken !== currentToken) {
      return Promise.reject(error)
    }
    clearAuthSession()
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
  }

  return Promise.reject(error)
}
