import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, setOnUnauthorized } from './api'
import { useAuthStore } from '../store/useAuthStore'
import { useAppStore } from '../store/useAppStore'

function response(status: number, body: unknown = {}, headers: Record<string, string> = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(headers),
    json: async () => body,
  } as Response
}

const CSRF = ['csrf', 'value'].join('-')

function signIn() {
  useAuthStore.setState(state => ({
    expiresAt: Date.now() + 60_000,
    sessionEpoch: state.sessionEpoch + 1,
    user: { user_id: 1, email: 'user@example.com', full_name: 'User' },
  }))
}

function calls() {
  return vi.mocked(fetch).mock.calls.map(([url, init]) => ({
    url: String(url),
    method: init?.method ?? 'GET',
    headers: new Headers(init?.headers),
    credentials: init?.credentials,
  }))
}

describe('requests in a cookie session (issue #524)', () => {
  beforeEach(() => {
    useAuthStore.setState({ expiresAt: null }) // signed out, without telling a server
    useAuthStore.getState().logout()
    useAppStore.getState().setCompany(null, null)
    document.cookie = `fabrique_csrf=${CSRF}; path=/`
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(200, { ok: true })))
  })

  afterEach(() => {
    setOnUnauthorized(null)
    document.cookie = 'fabrique_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
    vi.unstubAllGlobals()
  })

  it('sends cookies, never an Authorization header', async () => {
    signIn()
    await api.get('/v1/protected')

    const [request] = calls()
    expect(request.credentials).toBe('include')
    expect(request.headers.has('Authorization')).toBe(false)
    expect(request.headers.has('X-Fabrique-CSRF')).toBe(false) // a safe method
  })

  it('sends the CSRF header on unsafe methods', async () => {
    signIn()
    await api.post('/v1/things', { a: 1 })
    await api.delete('/v1/things/1')

    for (const request of calls()) {
      expect(request.headers.get('X-Fabrique-CSRF')).toBe(CSRF)
    }
  })

  it('asks the API for the CSRF value once when the page cannot read its cookie', async () => {
    document.cookie = 'fabrique_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
    signIn()
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(403, { detail: 'CSRF token missing or invalid' }, { 'X-Fabrique-CSRF': 'required' }))
      .mockResolvedValueOnce(response(200, { csrf_token: CSRF }))
      .mockResolvedValueOnce(response(200, { ok: true }))

    await expect(api.post('/v1/things', {})).resolves.toEqual({ ok: true })

    const [first, csrf, resent] = calls()
    expect(first.headers.has('X-Fabrique-CSRF')).toBe(false)
    expect(csrf.url).toContain('/v1/auth/csrf')
    expect(csrf.credentials).toBe('include')
    expect(resent.headers.get('X-Fabrique-CSRF')).toBe(CSRF)
  })

  it('does not ask for the CSRF value on an ordinary 403', async () => {
    signIn()
    vi.mocked(fetch).mockResolvedValue(response(403, { detail: 'Requires Owner role' }))

    await expect(api.post('/v1/things', {})).rejects.toMatchObject({ status: 403 })
    expect(fetch).toHaveBeenCalledOnce()
  })

  it('refreshes once on a 401 and retries the request', async () => {
    signIn()
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(401))
      .mockResolvedValueOnce(response(200, { expires_in: 900 }))
      .mockResolvedValueOnce(response(200, { ok: 'again' }))

    await expect(api.get('/v1/protected')).resolves.toEqual({ ok: 'again' })

    const [, refresh, retry] = calls()
    expect(refresh.url).toContain('/v1/auth/refresh')
    expect(refresh.method).toBe('POST')
    expect(refresh.credentials).toBe('include')
    expect(refresh.headers.get('X-Fabrique-CSRF')).toBe(CSRF)
    expect(retry.url).toContain('/v1/protected')
    expect(useAuthStore.getState().expiresAt).not.toBeNull()
  })

  it('retries when another tab refreshed first (409)', async () => {
    signIn()
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(401))
      .mockResolvedValueOnce(response(409))
      .mockResolvedValueOnce(response(200, { ok: true }))

    await expect(api.get('/v1/protected')).resolves.toEqual({ ok: true })
    expect(useAuthStore.getState().expiresAt).not.toBeNull()
  })

  it('signs out and calls the hook when the refresh is refused, but not for a login 401', async () => {
    const onUnauthorized = vi.fn()
    setOnUnauthorized(onUnauthorized)
    signIn()
    vi.mocked(fetch).mockResolvedValue(response(401))

    await expect(api.get('/v1/protected')).rejects.toBeInstanceOf(ApiError)
    expect(useAuthStore.getState().expiresAt).toBeNull()
    expect(useAuthStore.getState().user).toBeNull()
    expect(onUnauthorized).toHaveBeenCalledOnce()

    signIn()
    onUnauthorized.mockClear()
    vi.mocked(fetch).mockClear()
    await expect(api.login('user@example.com', 'wrong')).rejects.toBeInstanceOf(ApiError)
    expect(calls().map(call => call.url).some(url => url.includes('/v1/auth/refresh'))).toBe(false)
    expect(useAuthStore.getState().expiresAt).not.toBeNull()
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('signs out when the retried request is refused again', async () => {
    const onUnauthorized = vi.fn()
    setOnUnauthorized(onUnauthorized)
    signIn()
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(401))
      .mockResolvedValueOnce(response(200))
      .mockResolvedValueOnce(response(401))

    await expect(api.get('/v1/protected')).rejects.toBeInstanceOf(ApiError)
    expect(fetch).toHaveBeenCalledTimes(3) // one refresh, one retry, no loop
    expect(useAuthStore.getState().expiresAt).toBeNull()
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('refreshes once for parallel 401s and signs out once (issue #1)', async () => {
    const onUnauthorized = vi.fn()
    setOnUnauthorized(onUnauthorized)
    signIn()
    let rejectRequests!: (value: Response) => void
    const pending = new Promise<Response>(resolve => { rejectRequests = resolve })
    vi.mocked(fetch).mockImplementation(async url =>
      String(url).includes('/v1/auth/refresh') ? response(401) : pending,
    )

    const requests = Promise.allSettled([api.get('/v1/first'), api.get('/v1/second')])
    rejectRequests(response(401))
    expect((await requests).every(result => result.status === 'rejected')).toBe(true)

    expect(calls().filter(call => call.url.includes('/v1/auth/refresh'))).toHaveLength(1)
    expect(useAuthStore.getState().expiresAt).toBeNull()
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('does not end a newer session for a stale 401 (issue #1)', async () => {
    const onUnauthorized = vi.fn()
    setOnUnauthorized(onUnauthorized)
    signIn()
    let rejectRequest!: (value: Response) => void
    vi.mocked(fetch).mockReturnValue(new Promise(resolve => { rejectRequest = resolve }))
    const request = api.get('/v1/protected')
    signIn() // a new sign-in while the old request is in flight

    rejectRequest(response(401))
    await expect(request).rejects.toBeInstanceOf(ApiError)

    expect(useAuthStore.getState().expiresAt).not.toBeNull()
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('neither refreshes nor calls the hook without a session (issue #1)', async () => {
    const onUnauthorized = vi.fn()
    setOnUnauthorized(onUnauthorized)
    vi.mocked(fetch).mockResolvedValue(response(401))

    await expect(api.get('/v1/protected')).rejects.toBeInstanceOf(ApiError)

    expect(fetch).toHaveBeenCalledOnce()
    expect(onUnauthorized).not.toHaveBeenCalled()
  })
})
