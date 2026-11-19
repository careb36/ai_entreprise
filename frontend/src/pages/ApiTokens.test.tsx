import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ApiTokens from './ApiTokens'
import { grantableRoles } from './apiTokens/CreateTokenForm'
import { tokenState } from '../services/apiTokens'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const base = {
  scopes: [], created_by: 'manager@example.invalid', created_at: '2026-11-01T10:00:00+00:00',
  expires_at: '2027-01-30T10:00:00+00:00', expired: false, revoked_at: null, last_used_at: null,
}
const tokens = [
  { ...base, id: 1, name: 'ci-nightly', role: 'Viewer' },
  { ...base, id: 2, name: 'old-deploy', role: 'Manager', revoked_at: '2026-11-10T10:00:00+00:00' },
  { ...base, id: 3, name: 'stale', role: 'Viewer', expired: true },
]

function routed() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname === '/v1/auth/tokens' && init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      return response(200, { ...base, id: 9, name: body.name, role: body.role, token: 'fab_once_only_value' })
    }
    if (pathname === '/v1/auth/tokens') return response(200, { tokens })
    if (pathname === '/v1/auth/tokens/1/revoke') return response(200, { revoked: true })
    return response(404)
  })
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <ApiTokens />
    </QueryClientProvider>,
  )
}

describe('ApiTokens', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows a viewer the tokens and their state, with no create form and no revoke', async () => {
    vi.stubGlobal('fetch', routed())
    renderAs('Viewer')
    const table = await screen.findByRole('table', { name: 'Tokens' })
    expect(within(table).getByText('ci-nightly')).toBeInTheDocument()
    expect(within(table).getByText('Active')).toBeInTheDocument()
    expect(within(table).getByText('Revoked')).toBeInTheDocument()
    expect(within(table).getByText('Expired')).toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Create a token' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Revoke/ })).not.toBeInTheDocument()
  })

  it('creates a token and shows it once, then forgets it when dismissed', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'ci-release' } })
    fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'Manager' } })
    fireEvent.change(screen.getByLabelText('Expires after'), { target: { value: '30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create token' }))

    expect(await screen.findByDisplayValue('fab_once_only_value')).toBeInTheDocument()
    const post = fetch.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ company_id: 4, name: 'ci-release', role: 'Manager', expires_in_days: 30 })
    expect(window.localStorage.getItem('fab_once_only_value')).toBeNull()
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain('fab_once_only_value')

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss the new token' }))
    expect(screen.queryByDisplayValue('fab_once_only_value')).not.toBeInTheDocument()
  })

  it('never offers a token without expiry', async () => {
    vi.stubGlobal('fetch', routed())
    renderAs('Owner')
    const expiry = await screen.findByLabelText('Expires after')
    expect(within(expiry).getAllByRole('option').map(o => o.textContent)).toEqual(['30 days', '90 days', '180 days', '365 days'])
  })

  it('asks before revoking, and revokes an active token', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke the token ci-nightly' }))
    expect(screen.getByText('Revoke it now? Calls with it stop at once.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Yes, revoke' }))
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => String(url).includes('/v1/auth/tokens/1/revoke?company_id=4'))).toBe(true))
    // Only an active token can be revoked.
    expect(screen.queryByRole('button', { name: 'Revoke the token old-deploy' })).not.toBeInTheDocument()
  })

  it('shows the load error with a retry', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(500, { detail: 'boom' })))
    renderAs('Viewer')
    expect(await screen.findByRole('alert', {}, { timeout: 5000 })).toHaveTextContent('Could not load the tokens')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

describe('token helpers', () => {
  it('never lets a person grant a role above their own', () => {
    expect(grantableRoles('Viewer')).toEqual(['Viewer'])
    expect(grantableRoles('Manager')).toEqual(['Viewer', 'Manager'])
    expect(grantableRoles('Owner')).toEqual(['Viewer', 'Manager'])
  })

  it('reads revoked before expired', () => {
    expect(tokenState({ ...base, id: 1, name: 'x', role: 'Viewer', expired: true, revoked_at: '2026-11-10' })).toBe('revoked')
  })
})
