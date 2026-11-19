import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SsoMemberIdentities } from './SsoMemberIdentities'
import { queryClient } from '../lib/queryClient'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const identities = [
  { id: 1, user_id: 1, user_email: 'owner@example.invalid', email: 'owner@idp.example', linked_at: '2026-11-01T10:00:00+00:00', last_login_at: null },
  { id: 2, user_id: 2, user_email: 'dana@example.invalid', email: 'dana@idp.example', linked_at: '2026-11-02T10:00:00+00:00', last_login_at: '2026-11-18T10:00:00+00:00' },
]

function routed(unlink: Response = response(200, { unlinked: true, other_sign_in: true, sessions_ended: 2 })) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname === '/v1/companies/4/sso/identities') return response(200, { identities })
    if (pathname === '/v1/companies/4/sso/identities/2' && init?.method === 'DELETE') return unlink
    return response(404)
  })
}

function renderPanel() {
  useAuthStore.setState({
    expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: 'Owner' }],
    user: { user_id: 1, email: 'owner@example.invalid', full_name: 'Owner' },
  })
  return render(<QueryClientProvider client={queryClient}><SsoMemberIdentities companyId={4} /></QueryClientProvider>)
}

describe('SsoMemberIdentities', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('lists the members linked through the provider, without an unlink on the owner\'s own identity', async () => {
    vi.stubGlobal('fetch', routed())
    renderPanel()
    const table = await screen.findByRole('table', { name: 'Members\' single sign-on identities' })
    expect(within(table).getByText('dana@idp.example')).toBeInTheDocument()
    expect(within(table).getByText('Yours: unlink it from Account')).toBeInTheDocument()
    expect(within(table).queryByRole('button', { name: 'Unlink the identity of owner@example.invalid' })).not.toBeInTheDocument()
  })

  it('unlinks only after a confirmation that says their single sign-on sessions end', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Unlink the identity of dana@example.invalid' }))
    expect(screen.getByText(/sessions they started through single sign-on end now/)).toBeInTheDocument()
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, unlink' }))
    await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => init?.method === 'DELETE' && String(url).endsWith('/sso/identities/2'))).toBe(true))
  })

  it('explains a refusal the backend names by code', async () => {
    vi.stubGlobal('fetch', routed(response(404, { detail: 'not_found' })))
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Unlink the identity of dana@example.invalid' }))
    fireEvent.click(screen.getByRole('button', { name: 'Yes, unlink' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This identity is no longer linked through the company')
  })
})
