import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AuditAnchors from './AuditAnchors'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const coverage = {
  company_id: 4,
  status: 'uncovered_tail',
  head_seq: 9,
  covered_through: 6,
  verified_through: 4,
  segments: [{ from: 1, to: 4, strength: 'verified' }, { from: 5, to: 6, strength: 'claim' }],
  uncovered: { from: 7, to: 9 },
  failures: [],
  anchors: [
    { id: 1, kind: 'tsa', seq: 4, chain_hash: 'h4', anchored_at: '2026-11-15T10:00:00+00:00', reason: 'cadence', status: 'verified', state: 'verified', problem: null, external_ref: null, gen_time: null, tsa_cert_sha256: null },
    { id: 2, kind: 'webhook', seq: 6, chain_hash: 'h6', anchored_at: '2026-11-15T11:00:00+00:00', reason: 'sprint_close', status: 'delivered', state: 'claim', problem: null, external_ref: null, gen_time: null, tsa_cert_sha256: null },
  ],
}

const settings = {
  company_id: 4,
  enabled: true,
  cadence_minutes: 60,
  publish_webhook: true,
  webhook_configured: true,
  tsa_url: 'https://tsa.example.com/',
  tsa_cert_sha256: 'ab'.repeat(32),
  customer_keys: [{ fingerprint: 'ed25519:00ff', public_key: 'pem', added_at: '2026-11-15', retired_at: null }],
  updated_by: 'human:owner@example.invalid',
  updated_at: '2026-11-15T09:00:00',
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuditAnchors />
    </QueryClientProvider>,
  )
}

function routed(overrides: Record<string, unknown> = {}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname
    if (path === '/v1/audit/anchors/settings') return response(200, init?.method === 'PUT' ? settings : (overrides.settings ?? settings))
    if (path === '/v1/audit/anchors') return response(200, init?.method === 'POST' ? { queued: [{ id: 3 }], coverage } : (overrides.coverage ?? coverage))
    return response(404)
  })
}

describe('AuditAnchors', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('tells a viewer it is for owners and managers, without asking the backend', () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    renderAs('Viewer')
    expect(screen.getByRole('status')).toHaveTextContent('Only owners and managers see the audit anchors. Your role here is Viewer.')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('shows the coverage and the uncovered tail', async () => {
    vi.stubGlobal('fetch', routed())
    renderAs('Manager')
    expect(await screen.findByText('Anchored, with newer rows not covered yet.')).toBeInTheDocument()
    expect(screen.getByText('Rows 7 to 9 are not covered by any external anchor yet.')).toBeInTheDocument()
    expect(screen.getByText('claim (the endpoint\'s copy)')).toBeInTheDocument()
    expect(await screen.findByText('Only an owner changes these settings.')).toBeInTheDocument()
  })

  it('flags an anchor that no longer matches', async () => {
    vi.stubGlobal('fetch', routed({
      coverage: { ...coverage, status: 'anchor_mismatch', failures: [{ anchor_id: 1, kind: 'tsa', seq: 4, reason: 'hash_mismatch' }] },
    }))
    renderAs('Owner')
    expect(await screen.findByText('Timestamp anchor at row 4 no longer matches: hash_mismatch')).toBeInTheDocument()
  })

  it('lets an owner retire a key and a manager anchor now', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    fireEvent.click(await screen.findByRole('button', { name: 'Retire' }))
    await screen.findByText('Saved.')
    const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')!
    expect(JSON.parse(put[1]!.body as string)).toMatchObject({ retire_fingerprint: 'ed25519:00ff', enabled: true })
    expect(JSON.parse(put[1]!.body as string)).not.toHaveProperty('tsa_cert_pem')

    fireEvent.click(screen.getByRole('button', { name: 'Anchor now' }))
    expect(await screen.findByText('1 anchor(s) queued.')).toBeInTheDocument()
    const post = fetch.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(new URL(post[0]).searchParams.get('company_id')).toBe('4')
  })
})
