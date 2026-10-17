import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, setOnUnauthorized } from './api'
import { useAuthStore } from '../store/useAuthStore'
import { useAppStore } from '../store/useAppStore'

function response(status: number, body: unknown = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

describe('authenticated API requests', () => {
  beforeEach(() => {
    useAuthStore.getState().logout()
    useAppStore.getState().setCompany(null, null)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(200, { ok: true })))
  })

  afterEach(() => {
    setOnUnauthorized(null)
    vi.unstubAllGlobals()
  })

  it('sends the bearer token when a valid token is stored', async () => {
    useAuthStore.setState({ token: 'valid-token', expiresAt: Date.now() + 60_000 })

    await api.get('/v1/protected')

    const request = vi.mocked(fetch).mock.calls[0][1]!
    expect(new Headers(request.headers).get('Authorization')).toBe(
      ['Bearer', ['valid', 'token'].join('-')].join(' '),
    )
  })

  it('omits the bearer token when no token is stored', async () => {
    await api.get('/v1/public')

    const request = vi.mocked(fetch).mock.calls[0][1]!
    expect(new Headers(request.headers).has('Authorization')).toBe(false)
  })

  it('treats an expired token as absent', async () => {
    useAuthStore.setState({ token: 'expired-token', expiresAt: Date.now() - 1 })

    await api.get('/v1/protected')

    const request = vi.mocked(fetch).mock.calls[0][1]!
    expect(new Headers(request.headers).has('Authorization')).toBe(false)
    expect(useAuthStore.getState().token).toBeNull()
  })

  it('clears auth and invokes the hook for protected 401s, but not login 401s', async () => {
    const onUnauthorized = vi.fn()
    setOnUnauthorized(onUnauthorized)
    useAuthStore.setState({
      token: 'valid-token',
      expiresAt: Date.now() + 60_000,
      user: { user_id: 1, email: 'user@example.com', full_name: 'User' },
    })
    vi.mocked(fetch).mockResolvedValue(response(401))

    await expect(api.get('/v1/protected')).rejects.toBeInstanceOf(ApiError)
    expect(useAuthStore.getState().token).toBeNull()
    expect(onUnauthorized).toHaveBeenCalledOnce()

    useAuthStore.setState({ token: 'still-valid', expiresAt: Date.now() + 60_000 })
    onUnauthorized.mockClear()
    await expect(api.login('user@example.com', 'wrong')).rejects.toBeInstanceOf(ApiError)
    expect(useAuthStore.getState().token).toBe('still-valid')
    expect(onUnauthorized).not.toHaveBeenCalled()
  })
})
