import assert from 'node:assert/strict'

async function loadHelper() {
  try {
    const mod = await import('../src/api/auth-helpers.js')
    return mod.applyAuthToRequestConfig
  } catch {
    return null
  }
}

const applyAuthToRequestConfig = await loadHelper()

assert.equal(
  typeof applyAuthToRequestConfig,
  'function',
  'applyAuthToRequestConfig should exist'
)

const withToken = applyAuthToRequestConfig({ headers: {} }, 'test-token')
assert.equal(withToken.headers.Authorization, 'Bearer test-token')

const withoutToken = applyAuthToRequestConfig({ headers: {} }, '')
assert.equal(withoutToken.headers.Authorization, undefined)

console.log('auth-request-config test passed')

const helperModule = await import('../src/api/auth-helpers.js')
let redirectCalled = false
const originalLocation = globalThis.window
globalThis.window = {
  localStorage: { removeItem() {} },
  location: {
    pathname: '/dashboard',
    set href(value) {
      redirectCalled = value
    },
  },
}

await helperModule.handleAuthError({
  config: { skipAuthRedirect: true },
  response: { status: 401 },
}).catch(() => {})
assert.equal(redirectCalled, false)
globalThis.window = originalLocation

console.log('auth 401 probe test passed')
