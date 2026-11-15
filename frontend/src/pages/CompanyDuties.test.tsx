import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CompanyDuties from './CompanyDuties'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const policy = {
  company_id: 4,
  mode: 'high_risk',
  modes: ['off', 'high_risk', 'all'],
  high_risk_actions: ['accept_held_delivery', 'approve_specification', 'release'],
  all_actions: [],
  solo_exception: false,
  solo_reason: null,
  solo_granted_by: null,
  solo_granted_at: null,
  solo_eligible: true,
  solo_in_force: false,
  approvers: 1,
  review_interval_days: 90,
  inactive_days: 90,
  updated_by: null,
  updated_at: null,
}

const review = {
  company_id: 4,
  schedule: { due_at: '2026-10-01T00:00:00', state: 'missed', grace_days: 14, interval_days: 90, inactive_days: 90, last_completed_at: null, last_on_time: null },
  members: [
    { kind: 'member', id: 11, label: 'Ana', email: 'ana@example.test', own_role: 'Viewer', teams: [{ name: 'Release', role: 'Manager' }], effective_role: 'Manager', sso_linked: false, chat_links: ['slack'], last_activity: null, inactive: true },
  ],
  tokens: [
    { kind: 'token', id: 3, label: 'ci', role: 'Viewer', created_by: 'ana@example.test', created_at: null, expires_at: null, last_activity: null, inactive: true },
  ],
  history: [],
}

const approval = {
  action: 'accept_held_delivery',
  subject: { kind: 'task', id: 9, label: 'Encrypt stored credentials' },
  approver: { user_id: 11, label: 'Ana' },
  requesters: [{ user_id: 11, label: 'Ana', why: 'started the sprint' }],
  status: 'violation',
  conflicts: ['started the sprint'],
  at: '2026-11-01T10:00:00',
}

const report = {
  company_id: 4,
  mode: 'high_risk',
  summary: { separated: 2, exception: 0, violation: 1, unattributed: 0, not_a_person: 0 },
  approvals: [approval],
  violations: [approval],
  refusals: [],
  matrix: {
    duties: ['request', 'plan', 'deliver', 'verify', 'approve', 'release'],
    members: [{ user_id: 11, label: 'Ana', effective_role: 'Manager', member: true, request: 2, plan: 0, deliver: 0, verify: 0, approve: 1, release: 0, holds_request_and_approval: true }],
    roles: [],
    agents: [{ role: 'QA', request: 0, plan: 0, deliver: 0, verify: 2, approve: 0, release: 0 }],
  },
}

const accounts = {
  company_id: 4,
  generated_at: '2026-11-15T00:00:00',
  company: [{ kind: 'api_token', name: 'ci', owner: 'ana@example.test', scope: 'Viewer; all scopes', age_days: 3, status: 'active' }],
  deployment: [{ kind: 'llm_proxy_token', name: 'LLM_PROXY_INTERNAL_TOKEN', scope: 'backend to LLM proxy', present: true }],
}

function api(url: string, init?: RequestInit) {
  if (init?.method === 'POST' && url.includes('/access-review')) return response(201, { id: 1, on_time: false })
  if (init?.method === 'POST' && url.includes('/sod/exception')) return response(200, { ...policy, solo_exception: true, solo_in_force: true })
  if (url.includes('/sod/policy')) return response(200, policy)
  if (url.includes('/sod/report')) return response(200, report)
  if (url.includes('/access-review')) return response(200, review)
  if (url.includes('/service-accounts')) return response(200, accounts)
  return response(404)
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({
    expiresAt: Date.now() + 60_000,
    memberships: [{ company_id: 4, human_role: role, effective_role: role }],
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <CompanyDuties />
    </QueryClientProvider>,
  )
}

const calls = (fetch: ReturnType<typeof vi.fn>, method: string) =>
  fetch.mock.calls.filter(c => (c[1] as RequestInit | undefined)?.method === method)

describe('CompanyDuties', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('tells a manager only owners manage duties, without asking the backend', () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => api(url, init))
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    expect(screen.getByRole('status')).toHaveTextContent('Only owners manage duties and access reviews. Your role here is Manager.')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('lists a violation with its requester and approver, and the matrix from the records', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => api(url, init)))
    renderAs('Owner')
    const section = await screen.findByRole('region', { name: 'Approvals and duties report' })
    const violations = within(section).getByRole('table', { name: 'Violations and exceptions' })
    const row = within(violations).getByText('Encrypt stored credentials').closest('tr') as HTMLElement
    expect(within(row).getAllByRole('cell').map(c => c.textContent)).toEqual([
      'Accept a held delivery', 'Encrypt stored credentials', 'Ana', 'Ana', 'Violation',
    ])
    const matrix = within(section).getByRole('table', { name: 'Who did which duty' })
    const ana = within(matrix).getByText('Ana').closest('tr') as HTMLElement
    expect(within(ana).getByText('requests and approves')).toBeInTheDocument()
    expect(within(matrix).getByText('QA')).toBeInTheDocument()
  })

  it('flags a missed review and sends a decision for every account', async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => api(url, init))
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    const section = await screen.findByRole('region', { name: 'Access review' })
    expect(within(section).getByRole('alert')).toHaveTextContent('was missed')
    expect(within(section).getAllByText('Inactive')).toHaveLength(2)
    const submit = within(section).getByRole('button', { name: 'Complete the review' })
    expect(submit).toBeDisabled()

    fireEvent.click(within(within(section).getByRole('group', { name: 'Decision for Ana' })).getByLabelText('Confirm'))
    expect(submit).toBeDisabled()
    fireEvent.click(within(within(section).getByRole('group', { name: 'Decision for ci' })).getByLabelText('Remove'))
    fireEvent.click(submit)

    await vi.waitFor(() => expect(calls(fetch, 'POST')).toHaveLength(1))
    const [url, init] = calls(fetch, 'POST')[0]
    expect(String(url)).toContain('/v1/companies/4/access-review')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      decisions: [
        { kind: 'member', id: 11, decision: 'confirm' },
        { kind: 'token', id: 3, decision: 'remove' },
      ],
      note: null,
    })
  })

  it('grants the single-person exception only with a reason', async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => api(url, init))
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    const form = await screen.findByRole('form', { name: 'Single-person exception' })
    const grant = within(form).getByRole('button', { name: 'Grant the exception' })
    expect(grant).toBeDisabled()
    fireEvent.change(within(form).getByLabelText('Reason (kept in the audit)'), { target: { value: 'One person runs it.' } })
    fireEvent.click(grant)
    await vi.waitFor(() => expect(calls(fetch, 'POST')).toHaveLength(1))
    const [url, init] = calls(fetch, 'POST')[0]
    expect(String(url)).toContain('/v1/companies/4/sod/exception')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ reason: 'One person runs it.' })
  })

  it('lists service accounts by name and the proxy token by presence only', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => api(url, init)))
    renderAs('Owner')
    const section = await screen.findByRole('region', { name: 'Service accounts' })
    expect(within(section).getByText('API token')).toBeInTheDocument()
    expect(within(section).getByText('3 days')).toBeInTheDocument()
    expect(within(section).getByText('LLM proxy internal token: set')).toBeInTheDocument()
  })
})
