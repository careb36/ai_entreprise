import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type AuthProfile } from '../services/api'
import { useAuthStore } from './useAuthStore'

const profile: AuthProfile = {
  user_id: 1,
  email: 'old@example.com',
  full_name: 'Old User',
  memberships: [{ company_id: 14, human_role: 'Owner' }],
}

function newSession() {
  useAuthStore.setState(state => ({ expiresAt: Date.now() + 60_000, sessionEpoch: state.sessionEpoch + 1 }))
}

describe('profile session isolation', () => {
  beforeEach(() => {
    useAuthStore.getState().logout()
    useAuthStore.setState({ expiresAt: Date.now() + 60_000 })
    vi.spyOn(api, 'logout').mockResolvedValue(undefined)
  })

  afterEach(() => {
    useAuthStore.getState().logout()
    vi.restoreAllMocks()
  })

  it('discards a profile that resolves after logout (issue #2)', async () => {
    let resolveProfile!: (value: AuthProfile) => void
    vi.spyOn(api, 'me').mockReturnValue(new Promise(resolve => { resolveProfile = resolve }))
    const refresh = useAuthStore.getState().loadMe()

    useAuthStore.getState().logout()
    resolveProfile(profile)
    await refresh

    expect(useAuthStore.getState()).toMatchObject({
      expiresAt: null, user: null, memberships: [], meLoaded: false, loadingMe: false,
    })
  })

  it('discards an old profile and loading update after a session change (issue #2)', async () => {
    let resolveProfile!: (value: AuthProfile) => void
    vi.spyOn(api, 'me').mockReturnValue(new Promise(resolve => { resolveProfile = resolve }))
    const refresh = useAuthStore.getState().loadMe()
    newSession()
    useAuthStore.setState({ loadingMe: true })

    resolveProfile(profile)
    await refresh

    expect(useAuthStore.getState()).toMatchObject({
      user: null, memberships: [], meLoaded: false, loadingMe: true,
    })
  })

  it('discards old errors and loading updates after a session change (issue #2)', async () => {
    let rejectProfile!: (error: Error) => void
    vi.spyOn(api, 'me').mockReturnValue(new Promise((_, reject) => { rejectProfile = reject }))
    const refresh = useAuthStore.getState().loadMe()
    newSession()
    useAuthStore.setState({ loadingMe: true, profileError: null })

    rejectProfile(new Error('Old session failed'))
    await refresh.catch(() => {})

    expect(useAuthStore.getState()).toMatchObject({ loadingMe: true, profileError: null })
  })
})

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

/** Every value the page keeps in Web Storage, as text. */
function storedText(): string {
  const parts: string[] = []
  for (const storage of [window.localStorage, window.sessionStorage]) {
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i)!
      parts.push(key, storage.getItem(key) ?? '')
    }
  }
  return parts.join('\n')
}

describe('no credential in the browser (issue #524)', () => {
  // The login answer a browser gets: lifetimes and the CSRF value only. The
  // access and refresh tokens travel in HttpOnly cookies the page never sees.
  const csrf = ['csrf', 'value', 'from', 'login'].join('-')
  const loginBody = { token_type: 'cookie', expires_in: 900, session_expires_in: 604800, csrf_token: csrf }

  beforeEach(() => {
    window.localStorage.clear()
    window.sessionStorage.clear()
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/v1/auth/login')) return response(200, loginBody)
      if (url.includes('/v1/auth/me')) return response(200, profile)
      return response(204)
    }))
  })

  afterEach(() => {
    useAuthStore.getState().logout()
    vi.unstubAllGlobals()
    window.localStorage.clear()
  })

  it('writes nothing credential-like to localStorage or sessionStorage', async () => {
    await useAuthStore.getState().login('old@example.com', 'a-password')

    expect(useAuthStore.getState().user?.email).toBe('old@example.com')
    expect(useAuthStore.getState().expiresAt).toBeGreaterThan(Date.now() + 600_000_000 - 60_000)
    const stored = storedText()
    expect(stored).toContain('fabrique-auth-storage') // the non-secret profile is kept
    expect(stored).not.toMatch(/token|refresh|password|bearer|secret|csrf/i)
    expect(stored).not.toContain(csrf)
    expect(stored).not.toContain('a-password')
    expect(stored).not.toMatch(/eyJ[\w-]+\.[\w-]+\.[\w-]+/) // no JWT
    expect(Object.keys(useAuthStore.getState())).not.toContain('token')
  })

  it('logs in with cookies and logs out on the server', async () => {
    await useAuthStore.getState().login('old@example.com', 'a-password')
    const login = vi.mocked(fetch).mock.calls[0]
    expect(login[1]?.credentials).toBe('include')

    useAuthStore.getState().logout()
    const logout = vi.mocked(fetch).mock.calls.at(-1)!
    expect(String(logout[0])).toContain('/v1/auth/logout')
    expect(logout[1]?.method).toBe('POST')
    expect(logout[1]?.credentials).toBe('include')
    expect(new Headers(logout[1]?.headers).get('X-Fabrique-CSRF')).toBe(csrf)
    expect(useAuthStore.getState()).toMatchObject({ expiresAt: null, user: null, memberships: [] })
  })
})
