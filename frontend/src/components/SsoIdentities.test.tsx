import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SsoIdentities } from './SsoIdentities'
import { queryClient } from '../lib/queryClient'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const MINE = {
  identities: [
    { id: 7, company_id: 1, company_name: 'Acme', email: 'ana@acme.test', linked_at: '2026-11-18T10:00:00', last_login_at: null },
  ],
  linkable: [{ company_id: 2, company_name: 'Globex', preset: 'google' }],
  has_password: true,
}

function stubApi(overrides: Record<string, () => Response> = {}) {
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const { pathname } = new URL(url)
    const key = `${init.method ?? 'GET'} ${pathname}`
    if (overrides[key]) return overrides[key]()
    if (key === 'GET /v1/auth/sso/identities') return response(200, MINE)
    return response(404, { detail: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderAt(entry = '/account') {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <SsoIdentities />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SsoIdentities', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    queryClient.clear()
  })

  it('lists the linked identities and the companies left to link', async () => {
    stubApi()
    renderAt()
    expect(await screen.findByText('Acme')).toBeInTheDocument()
    expect(screen.getByText('Globex')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Unlink$/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Link$/ })).toBeInTheDocument()
  })

  it('starts a link and sends the browser to the provider', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign })
    const fetch = stubApi({
      'POST /v1/auth/sso/link/start': () => response(200, { authorization_url: 'https://idp.test/authorize?state=s' }),
    })
    renderAt()
    fireEvent.click(await screen.findByRole('button', { name: /^Link$/ }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://idp.test/authorize?state=s'))
    const call = fetch.mock.calls.find(([url]) => String(url).endsWith('/v1/auth/sso/link/start'))
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ company_id: 2 })
  })

  it('says why the last way to sign in cannot be unlinked', async () => {
    stubApi({
      'DELETE /v1/auth/sso/identities/7': () => response(409, { detail: 'last_sign_in_method' }),
    })
    renderAt()
    fireEvent.click(await screen.findByRole('button', { name: /^Unlink$/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/only way to sign in/)
  })

  it("shows what the provider's return brought", async () => {
    stubApi()
    renderAt('/account?sso_link_error=email_mismatch')
    expect(await screen.findByRole('alert')).toHaveTextContent(/not this account's email/)
  })
})
