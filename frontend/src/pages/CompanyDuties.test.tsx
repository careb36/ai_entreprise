import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
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

const protectedPaths = {
  company_id: 4,
  enabled: false,
  patterns: [{ pattern: 'bench/**', why: 'the exam and its answers' }],
}

function api(url: string, init?: RequestInit) {
  if (init?.method === 'PUT' && url.includes('/protected_paths')) return response(200, { ...protectedPaths, enabled: true })
  if (url.includes('/protected_paths')) return response(200, protectedPaths)
  if (init?.method === 'POST' && url.includes('/access-review')) return response(201, { id: 1, on_time: false })
  if (init?.method === 'POST' && url.includes('/sod/exception')) return response(200, { ...policy, solo_exception: true, solo_in_force: true })
  if (url.includes('/sod/policy')) return response(200, policy)
  if (url.includes('/sod/report')) return response(200, report)
  if (url.includes('/access-review')) return response(200, review)
  if (url.includes('/service-accounts')) return response(200, accounts)
  return response(404)
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer', path = '/company/duties') {
  useAuthStore.setState({
    expiresAt: Date.now() + 60_000,
    memberships: [{ company_id: 4, human_role: role, effective_role: role }],
  })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={queryClient}>
        <CompanyDuties />
      </QueryClientProvider>
    </MemoryRouter>,
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

  it('lists what the SSO group mapping keeps by hand and needs a decision on each', async () => {
    const withSso = {
      ...review,
      sso: [
        { kind: 'exclusion', id: 5, label: 'Bruno', user_id: 12, team_id: 7, team_name: 'Platform', created_by: 'Ana', created_at: null, age_days: 3, reason: 'Suspected account compromise', inactive: false },
        { kind: 'manager_pair', id: 6, label: 'leads', group_value: 'leads', team_id: 8, team_name: 'Leads', decided_by: 'Ana', decided_at: null, reason: 'only Owner', flagged: true, inactive: false },
      ],
    }
    const fetch = vi.fn(async (url: string, init?: RequestInit) =>
      init?.method !== 'POST' && url.includes('/access-review') ? response(200, withSso) : api(url, init))
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    const section = await screen.findByRole('region', { name: 'Access review' })
    expect(within(section).getByText('Excluded from Platform by Ana, 3 days ago: Suspected account compromise')).toBeInTheDocument()
    expect(within(section).getByText(/Group leads grants Manager in Leads, confirmed alone by Ana/)).toBeInTheDocument()
    for (const name of ['Ana', 'ci', 'Bruno']) {
      fireEvent.click(within(within(section).getByRole('group', { name: `Decision for ${name}` })).getByLabelText('Confirm'))
    }
    const submit = within(section).getByRole('button', { name: 'Complete the review' })
    expect(submit).toBeDisabled()
    fireEvent.click(within(within(section).getByRole('group', { name: 'Decision for leads' })).getByLabelText('Remove'))
    fireEvent.click(submit)
    await vi.waitFor(() => expect(calls(fetch, 'POST')).toHaveLength(1))
    expect(JSON.parse(String((calls(fetch, 'POST')[0][1] as RequestInit).body)).decisions).toEqual([
      { kind: 'member', id: 11, decision: 'confirm' },
      { kind: 'token', id: 3, decision: 'confirm' },
      { kind: 'exclusion', id: 5, decision: 'confirm' },
      { kind: 'manager_pair', id: 6, decision: 'remove' },
    ])
  })

  it('lists the exceptions to a standard and a single-Owner standards set, each decided', async () => {
    const withStandards = {
      ...review,
      standards: [
        { kind: 'standard_exception', id: 7, label: 'STD-SEC-1', standard_id: 'STD-SEC-1', reason: 'A vendored fixture', compensating_control: 'Hand review', expires_at: '2026-12-20T00:00:00', decided_by: 'Bruno', flagged: false, inactive: false },
        { kind: 'standards_single_owner', id: 2, label: 'v2', version: 2, decided_by: 'ana@example.test', flagged: true, inactive: false },
      ],
    }
    const fetch = vi.fn(async (url: string, init?: RequestInit) =>
      init?.method !== 'POST' && url.includes('/access-review') ? response(200, withStandards) : api(url, init))
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    const section = await screen.findByRole('region', { name: 'Access review' })
    expect(within(section).getByText(/Exception to STD-SEC-1 until .*, approved by Bruno: A vendored fixture \(compensating control: Hand review\)/)).toBeInTheDocument()
    expect(within(section).getByText(/Company standards v2 activated alone by ana@example.test.*decided by a single Owner/)).toBeInTheDocument()
    for (const name of ['Ana', 'ci', 'v2']) {
      fireEvent.click(within(within(section).getByRole('group', { name: `Decision for ${name}` })).getByLabelText('Confirm'))
    }
    const submit = within(section).getByRole('button', { name: 'Complete the review' })
    expect(submit).toBeDisabled()
    fireEvent.click(within(within(section).getByRole('group', { name: 'Decision for STD-SEC-1' })).getByLabelText('Remove'))
    fireEvent.click(submit)
    await vi.waitFor(() => expect(calls(fetch, 'POST')).toHaveLength(1))
    expect(JSON.parse(String((calls(fetch, 'POST')[0][1] as RequestInit).body)).decisions).toEqual([
      { kind: 'member', id: 11, decision: 'confirm' },
      { kind: 'token', id: 3, decision: 'confirm' },
      { kind: 'standard_exception', id: 7, decision: 'remove' },
      { kind: 'standards_single_owner', id: 2, decision: 'confirm' },
    ])
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

  it('shows the protected list read-only and lets the owner turn it on', async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => api(url, init))
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    const section = await screen.findByRole('region', { name: 'Protected paths' })
    expect(within(section).getByText('bench/**')).toBeInTheDocument()
    expect(within(section).getByText('The list in force (1 patterns)')).toBeInTheDocument()
    const box = within(section).getByRole('checkbox')
    expect(box).not.toBeChecked()
    fireEvent.click(box)
    await vi.waitFor(() => expect(calls(fetch, 'PUT')).toHaveLength(1))
    const [url, init] = calls(fetch, 'PUT')[0]
    expect(String(url)).toContain('/v1/companies/4/protected_paths')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ enabled: true })
    await vi.waitFor(() => expect(within(section).getByRole('checkbox')).toBeChecked())
  })

  it('lists service accounts by name and the proxy token by presence only', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => api(url, init)))
    renderAs('Owner')
    const section = await screen.findByRole('region', { name: 'Service accounts' })
    expect(within(section).getByText('API token')).toBeInTheDocument()
    expect(within(section).getByText('3 days')).toBeInTheDocument()
    expect(within(section).getByText('LLM proxy internal token: set')).toBeInTheDocument()
  })

  it("opens another company's policy from an owner action's link, and only that panel", async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => api(url, init))
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner', '/company/duties?company=3')
    expect(await screen.findByText(/policy of company 3, opened from an owner action/)).toBeInTheDocument()
    await screen.findByRole('form', { name: 'Single-person exception' })
    const asked = fetch.mock.calls.map(c => String(c[0]))
    expect(asked.some(u => u.includes('/v1/companies/3/sod/policy'))).toBe(true)
    // Nothing of the company she works in: the other panels are not shown.
    expect(asked.some(u => u.includes('/companies/4/'))).toBe(false)
  })
})
