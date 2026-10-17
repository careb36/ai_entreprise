import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import App from './App'
import { api, ApiError, setOnUnauthorized } from './services/api'
import { useAuthStore } from './store/useAuthStore'

describe('unauthorized navigation', () => {
  afterEach(() => {
    setOnUnauthorized(null)
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    window.history.replaceState(null, '', '/')
  })

  it('does not nest the return destination when already on login (issue #1)', async () => {
    useAuthStore.getState().logout()
    useAuthStore.setState({ token: 'rejected-token', expiresAt: Date.now() + 60_000 })
    window.history.replaceState(null, '', '/login?from=%2Fevidence%2F5')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Log in' })).toBeInTheDocument()

    await act(async () => {
      await expect(api.get('/v1/protected')).rejects.toBeInstanceOf(ApiError)
    })

    expect(useAuthStore.getState().token).toBeNull()
    expect(window.location.pathname).toBe('/login')
    expect(window.location.search).toBe('?from=%2Fevidence%2F5')
  })
})
