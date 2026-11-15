import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import Login from './Login'
import SsoComplete from './SsoComplete'
import { useAuthStore } from '../store/useAuthStore'

const { mockApi, MockApiError } = vi.hoisted(() => {
  class MockApiError extends Error {
    constructor(public status: number, message: string) {
      super(message)
    }
  }
  return {
    MockApiError,
    mockApi: { login: vi.fn(), me: vi.fn(), ssoExchange: vi.fn() },
  }
})

vi.mock('../services/api', () => ({
  api: mockApi,
  ApiError: MockApiError,
  ssoStartUrl: (key: string) => `http://backend.test/v1/auth/sso/${encodeURIComponent(key.trim())}/start`,
}))

function CurrentPath() {
  const location = useLocation()
  return <output data-testid="current-path">{location.pathname}</output>
}

function renderAt(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/login/sso" element={<SsoComplete />} />
        <Route path="/" element={<CurrentPath />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('sign in with SSO', () => {
  const originalLocation = window.location
  const assign = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.getState().logout()
    Object.defineProperty(window, 'location', { configurable: true, value: { ...originalLocation, assign } })
  })

  afterEach(() => {
    Object.defineProperty(window, 'location', { configurable: true, value: originalLocation })
  })

  it('sends the browser to the backend with the email or SSO name typed', () => {
    renderAt('/login')
    const button = screen.getByRole('button', { name: 'Sign in with SSO' })
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Work email or company SSO name'), { target: { value: ' ana@acme.test ' } })
    fireEvent.click(button)
    expect(assign).toHaveBeenCalledWith('http://backend.test/v1/auth/sso/ana%40acme.test/start')
    expect(mockApi.login).not.toHaveBeenCalled()
  })

  it('explains a password refused because the company requires SSO', async () => {
    mockApi.login.mockRejectedValue(new MockApiError(403, 'Forbidden'))
    renderAt('/login')
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@acme.test' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'pw' } })
    fireEvent.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Your company requires single sign-on')
  })

  it('trades the one-time code for a session and goes home', async () => {
    mockApi.ssoExchange.mockResolvedValue({ access_token: 'sso-token', token_type: 'bearer', expires_in: 3600 })
    mockApi.me.mockResolvedValue({ user_id: 3, email: 'ana@acme.test', full_name: 'Ana', memberships: [] })
    renderAt('/login/sso?code=one-time')
    expect(await screen.findByTestId('current-path')).toHaveTextContent('/')
    expect(mockApi.ssoExchange).toHaveBeenCalledTimes(1)
    expect(mockApi.ssoExchange).toHaveBeenCalledWith('one-time')
    expect(useAuthStore.getState().token).toBe('sso-token')
  })

  it('explains an error the callback sent back, and a code that no longer works', async () => {
    renderAt('/login/sso?error=domain_not_allowed')
    expect(screen.getByRole('alert')).toHaveTextContent('Your email domain is not allowed for this company.')
    expect(mockApi.ssoExchange).not.toHaveBeenCalled()

    mockApi.ssoExchange.mockRejectedValue(new MockApiError(401, 'Unauthorized'))
    renderAt('/login/sso?code=used')
    await waitFor(() => expect(screen.getAllByRole('alert').at(-1)).toHaveTextContent('invalid or expired'))
  })

  it('does not echo an unknown error code', () => {
    renderAt('/login/sso?error=<script>')
    expect(screen.getByRole('alert')).toHaveTextContent('The sign-in could not be completed. Try again.')
  })
})
