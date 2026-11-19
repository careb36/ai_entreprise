import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SecurityTests from './SecurityTests'
import { today } from './securityTests/WstgReviewForm'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const row = (id: string, name: string, status: string, extra: Record<string, unknown> = {}) => ({
  id, short: id.split('-').slice(-2).join('-'), name, category: 'Client-side', applicable: status !== 'not-applicable',
  mode: status === 'needs-manual' ? 'manual' : 'auto', reason: 'selected by the plan', status, evidence: null, unverifiable: [], ...extra,
})

const plan = {
  company_id: 4,
  catalogue: { commit: 'abc1234', licence: 'CC BY-SA 4.0', attribution: 'OWASP WSTG contributors' },
  signals: [],
  counts: { 'observed-pass': 1, 'observed-fail': 1, 'not-run': 0, 'needs-manual': 1, 'not-applicable': 0 },
  rows: [
    row('WSTG-v42-CLNT-07', 'Testing Cross Origin Resource Sharing', 'observed-fail', {
      evidence: { kind: 'verification', status: 'observed-fail', run_id: 31, observation: 'credentialed answer to any origin' },
    }),
    row('WSTG-v42-ATHN-03', 'Testing for Weak Lock Out Mechanism', 'needs-manual'),
    row('WSTG-v42-CONF-07', 'Test HTTP Strict Transport Security', 'observed-pass', {
      evidence: { kind: 'review', status: 'observed-pass', reviewer_role: 'Manager', reviewed_on: '2026-11-10', artifact: 'pentest report' },
    }),
  ],
}

function routed() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname === '/v1/companies/4/security/wstg') return response(200, plan)
    if (pathname === '/v1/companies/4/security/wstg/reviews' && init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      return response(201, { id: 1, ...body, reviewer_role: 'Manager', record_sha256: 'f'.repeat(64), created_at: null })
    }
    if (pathname === '/v1/companies/4/security/wstg/reviews') return response(200, { reviews: [] })
    return response(404)
  })
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><SecurityTests /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SecurityTests', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows a viewer the plan, what set each status, and no form', async () => {
    vi.stubGlobal('fetch', routed())
    renderAs('Viewer')
    const table = await screen.findByRole('table', { name: 'Plan' })
    expect(within(table).getByText('Testing Cross Origin Resource Sharing')).toBeInTheDocument()
    expect(within(table).getByText('Failed')).toBeInTheDocument()
    expect(within(table).getByRole('link', { name: 'run 31' })).toHaveAttribute('href', '/evidence/31')
    expect(within(table).getByText('Reviewed by a Manager on 2026-11-10')).toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Record a review' })).not.toBeInTheDocument()
    expect(screen.getByText(/CC BY-SA 4.0/)).toBeInTheDocument()
  })

  it('filters the plan by status', async () => {
    vi.stubGlobal('fetch', routed())
    renderAs('Viewer')
    await screen.findByRole('table', { name: 'Plan' })
    fireEvent.click(screen.getByRole('button', { name: /Needs a person/ }))
    const table = screen.getByRole('table', { name: 'Plan' })
    expect(within(table).getAllByRole('row')).toHaveLength(2)
    expect(within(table).getByText('Testing for Weak Lock Out Mechanism')).toBeInTheDocument()
  })

  it('lets a manager record a review, and requires a note for not applicable', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    const form = await screen.findByRole('form', { name: 'Record a review' })
    // The test waiting for a person comes first.
    expect(within(form).getByLabelText('Test')).toHaveValue('WSTG-v42-ATHN-03')
    fireEvent.change(within(form).getByLabelText("Reviewer's name"), { target: { value: 'A. Reviewer' } })
    fireEvent.change(within(form).getByLabelText('What it rests on'), { target: { value: 'ticket SEC-12' } })
    fireEvent.change(within(form).getByLabelText('Result'), { target: { value: 'not_applicable' } })
    const submit = within(form).getByRole('button', { name: 'Record the review' })
    expect(submit).toBeDisabled()
    fireEvent.change(within(form).getByLabelText('Why it does not apply (required)'), { target: { value: 'no login' } })
    fireEvent.click(submit)
    expect(await screen.findByText('Review of WSTG-v42-ATHN-03 recorded')).toBeInTheDocument()
    const post = fetch.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      wstg_id: 'WSTG-v42-ATHN-03', result: 'not_applicable', reviewer_name: 'A. Reviewer', reviewed_on: today(), artifact: 'ticket SEC-12', note: 'no login',
    })
    await waitFor(() => expect(within(form).getByLabelText('What it rests on')).toHaveValue(''))
  })

  it('never lets a review be dated in the future', async () => {
    vi.stubGlobal('fetch', routed())
    renderAs('Owner')
    const date = await screen.findByLabelText('Reviewed on')
    expect(date).toHaveAttribute('max', today())
  })
})
