import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CompanySso from './CompanySso'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

/** Answers the group mapping's own request (issue #703) apart, so a test's
 * sequence of answers is the SSO settings' alone. */
function skippingGroups(inner: (url: string, init?: RequestInit) => unknown) {
  return vi.fn(async (url: string, init?: RequestInit) =>
    String(url).includes('/sso/group-mappings') ? response(404, { detail: 'no SSO connection' }) : inner(url, init))
}

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const connection = {
  id: 1,
  company_id: 4,
  slug: 'acme',
  preset: 'generic',
  tenant: null,
  issuer: 'https://idp.example.com',
  client_id: 'test-client',
  has_client_secret: true,
  allowed_domains: ['acme.test'],
  default_role: 'Viewer',
  auto_join: false,
  enabled: true,
  require_sso: false,
  tested_at: null,
  test_ok: null,
  test_detail: null,
  redirect_uri: 'http://backend.test/v1/auth/sso/callback',
  updated_at: '2026-11-15T10:00:00',
}

const settings = (conn: unknown) => ({
  connection: conn,
  presets: ['generic', 'google', 'entra'],
  redirect_uri: 'http://backend.test/v1/auth/sso/callback',
  owner_signed_in: false,
})

function renderAs(role: 'Owner' | 'Manager') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <CompanySso />
    </QueryClientProvider>,
  )
}

describe('CompanySso', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('tells a non-owner that only owners manage it, without asking the backend', () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    expect(screen.getByRole('status')).toHaveTextContent('Only owners manage single sign-on. Your role here is Manager.')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('creates a connection with the fields entered and shows the redirect URI', async () => {
    const inner = vi.fn()
      .mockResolvedValueOnce(response(200, settings(null)))
      .mockResolvedValueOnce(response(200, connection))
      .mockResolvedValue(response(200, settings(connection)))
    vi.stubGlobal('fetch', skippingGroups(inner))
    renderAs('Owner')

    expect(await screen.findByText('http://backend.test/v1/auth/sso/callback')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('SSO name (on the login page)'), { target: { value: 'acme' } })
    fireEvent.change(screen.getByLabelText('Issuer URL'), { target: { value: 'https://idp.example.com' } })
    fireEvent.change(screen.getByLabelText('Client ID'), { target: { value: 'test-client' } })
    fireEvent.change(screen.getByLabelText('Client secret'), { target: { value: 'typed-in-the-form' } })
    fireEvent.change(screen.getByLabelText('Allowed email domains (comma-separated)'), { target: { value: 'acme.test, acme2.test' } })
    fireEvent.click(screen.getByLabelText('Enabled'))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('Saved.')).toBeInTheDocument()
    const [url, init] = inner.mock.calls[1]
    expect(new URL(url).pathname).toBe('/v1/companies/4/sso')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({
      slug: 'acme',
      preset: 'generic',
      tenant: null,
      issuer: 'https://idp.example.com',
      client_id: 'test-client',
      client_secret: 'typed-in-the-form',
      allowed_domains: ['acme.test', 'acme2.test'],
      default_role: 'Viewer',
      auto_join: false,
      enabled: true,
      require_sso: false,
    })
  })

  it('keeps the stored secret when the field is left empty, and runs the test', async () => {
    const inner = vi.fn()
      .mockResolvedValueOnce(response(200, settings(connection)))
      .mockResolvedValueOnce(response(200, {
        ok: true, detail: 'discovery and JWKS fetched',
        observed: { issuer: 'https://idp.example.com', key_count: 2 },
        connection: { ...connection, test_ok: true },
      }))
      .mockResolvedValue(response(200, settings(connection)))
    vi.stubGlobal('fetch', skippingGroups(inner))
    renderAs('Owner')

    const secret = await screen.findByLabelText('Client secret')
    expect(secret).toHaveValue('')
    expect(secret).toHaveAttribute('placeholder', 'Set — leave empty to keep it')
    expect(screen.getByText('Requiring SSO becomes possible once an owner has signed in through this connection.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText('Issuer https://idp.example.com, 2 signing key(s).')).toBeInTheDocument()
    expect(new URL(inner.mock.calls[1][0]).pathname).toBe('/v1/companies/4/sso/test')
  })

  it('shows only the tenant for the Entra preset', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(200, settings(null))))
    renderAs('Owner')
    fireEvent.change(await screen.findByLabelText('Provider'), { target: { value: 'entra' } })
    expect(screen.getByLabelText('Directory (tenant) ID')).toBeInTheDocument()
    expect(screen.queryByLabelText('Issuer URL')).not.toBeInTheDocument()
  })

  it('explains a refusal from the backend', async () => {
    const inner = vi.fn()
      .mockResolvedValueOnce(response(200, settings(connection)))
      .mockResolvedValueOnce(response(409, { detail: 'require_sso: sign in through it as an Owner first' }))
    vi.stubGlobal('fetch', skippingGroups(inner))
    renderAs('Owner')
    fireEvent.click(await screen.findByLabelText('Require SSO: turn off password sign-in for members'))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save: require_sso: sign in through it as an Owner first')
  })
})
