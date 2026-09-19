import { clearAuthSession, getAuthToken } from '../auth.js'

export function applyAuthToRequestConfig(config, token = getAuthToken()) {
  const nextConfig = { ...config, headers: { ...(config.headers || {}) } }

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
    clearAuthSession()
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
  }

  return Promise.reject(error)
}
