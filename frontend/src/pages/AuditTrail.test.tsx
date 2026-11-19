import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AuditTrail from './AuditTrail'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const anchors = { company_id: 4, status: 'covered', head_seq: 9, covered_through: 9, verified_through: 9, segments: [], uncovered: null, failures: [], anchors: [] }
const intact = { company_id: 4, status: 'intact', mode: 'hmac-sha256', rows: 9, head: { seq: 9, chain_hash: 'hmac-sha256:abc123' }, breaks: [], anchors }
const statement = {
  company_id: 4, seq: 9, chain_hash: 'hmac-sha256:abc123', anchored_at: '2026-11-19T10:00:00+00:00',
  statement: '{"anchored_at":"2026-11-19T10:00:00Z","chain_hash":"hmac-sha256:abc123","company_id":4,"seq":9,"type":"x","v":1}',
}

function routed(chain: unknown = intact, signature: Response = response(200, { id: 7, kind: 'customer_key', seq: 9, status: 'verified' })) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname === '/v1/audit/chain') return response(200, chain)
    if (pathname === '/v1/audit/anchors/statement') return response(200, statement)
    if (pathname === '/v1/audit/anchors/signature' && init?.method === 'POST') return signature
    return response(404)
  })
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><AuditTrail /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('AuditTrail', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('walks the chain only when asked, and shows an intact chain with its head', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Viewer')
    expect(fetch).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Verify the chain' }))
    expect(await screen.findByText('Intact')).toBeInTheDocument()
    expect(screen.getByText('hmac-sha256:abc123')).toBeInTheDocument()
    expect(screen.getByText('Keyed (HMAC-SHA256)')).toBeInTheDocument()
    // A viewer does not reach the anchors page, so it is not linked.
    expect(screen.queryByRole('link', { name: 'See the anchors' })).not.toBeInTheDocument()
  })

  it('lists where a broken chain breaks, as an alert', async () => {
    vi.stubGlobal('fetch', routed({ ...intact, status: 'broken', breaks: [{ seq: 4, reason: 'hash_mismatch' }, { seq: 5, reason: 'prev_mismatch' }] }))
    renderAs('Manager')
    fireEvent.click(screen.getByRole('button', { name: 'Verify the chain' }))
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText('The row or its place was altered')).toBeInTheDocument()
    expect(within(alert).getByText('The link to the previous row does not match')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'See the anchors' })).toHaveAttribute('href', '/company/audit-anchors')
  })

  it('submits a counter-signature for the statement it fetched', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Viewer')
    fireEvent.click(screen.getByRole('button', { name: 'Get the statement' }))
    expect(await screen.findByLabelText('Statement of row 9')).toHaveValue(statement.statement)
    fireEvent.change(screen.getByLabelText('Signature (base64)'), { target: { value: ' c2lnbmF0dXJl ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Submit the signature' }))
    expect(await screen.findByText('Row 9 is anchored with your key')).toBeInTheDocument()
    const post = fetch.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(String(post?.[0])).toContain('/v1/audit/anchors/signature?company_id=4')
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      seq: 9, chain_hash: 'hmac-sha256:abc123', anchored_at: '2026-11-19T10:00:00+00:00', signature: 'c2lnbmF0dXJl',
    })
  })

  it('refuses a signature that is not base64 before sending it, and shows the backend refusal', async () => {
    const fetch = routed(intact, response(422, { detail: 'signature: does not verify with any active customer key' }))
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    fireEvent.click(screen.getByRole('button', { name: 'Get the statement' }))
    const field = await screen.findByLabelText('Signature (base64)')
    fireEvent.change(field, { target: { value: 'not base64!' } })
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('button', { name: 'Submit the signature' })).toBeDisabled()
    fireEvent.change(field, { target: { value: 'AAAA' } })
    fireEvent.click(screen.getByRole('button', { name: 'Submit the signature' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('does not verify with any active customer key'))
  })
})
