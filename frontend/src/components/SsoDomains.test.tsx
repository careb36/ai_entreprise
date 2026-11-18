import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SsoDomains } from './SsoDomains'
import { queryClient } from '../lib/queryClient'
import { useAuthStore } from '../store/useAuthStore'
import type { SsoDomain } from '../services/api'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const pending: SsoDomain = {
  domain: 'acme.test',
  status: 'unverified',
  record_name: '_la-fabrique-verify.acme.test',
  record_value: 'la-fabrique-domain-verification=generated-by-the-backend',
  legacy: false,
  grace_until: null,
  verified_at: null,
  last_checked_at: null,
  last_outcome: null,
}

function renderWith(domains: SsoDomain[]) {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: 'Owner' }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <SsoDomains companyId={4} domains={domains} />
    </QueryClientProvider>,
  )
}

describe('SsoDomains', () => {
  beforeEach(() => queryClient.clear())

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows the TXT record to publish and the status in words', () => {
    renderWith([pending, { ...pending, domain: 'old.test', status: 'grace', legacy: true, grace_until: '2026-12-18T00:00:00' }])
    const row = screen.getByRole('listitem', { name: 'acme.test' })
    expect(within(row).getByText('_la-fabrique-verify.acme.test')).toBeInTheDocument()
    expect(within(row).getByText('la-fabrique-domain-verification=generated-by-the-backend')).toBeInTheDocument()
    expect(within(row).getByText('Not verified')).toBeInTheDocument()
    const legacy = screen.getByRole('listitem', { name: 'old.test' })
    expect(within(legacy).getByText('Grace period')).toBeInTheDocument()
    expect(within(legacy).getByText('Works unverified until')).toBeInTheDocument()
  })

  it('checks a domain and says what DNS answered', async () => {
    const fetch = vi.fn().mockResolvedValue(response(200, {
      outcome: 'no_match',
      answer: ['v=spf1 -all'],
      detail: null,
      domain: { ...pending, last_outcome: 'no_match', last_checked_at: '2026-11-18T10:00:00' },
    }))
    vi.stubGlobal('fetch', fetch)
    renderWith([pending])
    fireEvent.click(screen.getByRole('button', { name: 'Check' }))

    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent('The name exists but no TXT value matches the token.')
    expect(status).toHaveTextContent('DNS answered: v=spf1 -all')
    const [url, init] = fetch.mock.calls[0]
    expect(new URL(url).pathname).toBe('/v1/companies/4/sso/domains/acme.test/check')
    expect(init.method).toBe('POST')
  })
})
