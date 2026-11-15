import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Usage from './Usage'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, text: async () => String(body) } as Response
}

const zero = {
  tasks: 0, rework: 0, runs: 0, verified: 0, refuted: 0, unverified: 0, approvals: 0, changes_requested: 0,
  turns: 0, reported_turns: 0, estimated_turns: 0, unavailable_turns: 0, input_tokens: 0, output_tokens: 0,
  reported_tokens: 0, estimated_tokens: 0, cost_cents: null, cost_unknown_turns: 0,
}

const report = {
  company_id: 4,
  since: '2026-11-02',
  until: '2026-11-14',
  period: 'week',
  timezone: 'UTC',
  generated_at: '2026-11-15T12:00:00Z',
  columns: [],
  totals: { ...zero, tasks: 3, runs: 3, verified: 1, refuted: 1, reported_tokens: 1_250_015, cost_cents: 200, cost_unknown_turns: 3 },
  by_period: [{ ...zero, key: '2026-11-02', label: '2026-11-02', tasks: 1 }],
  by_person: [
    { ...zero, key: 11, label: 'Ana', tasks: 2, cost_cents: 200, cost_unknown_turns: 1 },
    { ...zero, key: null, label: null, tasks: 1, cost_unknown_turns: 2 },
  ],
  by_team: [{ ...zero, key: null, label: null }],
  by_role: [],
  by_producer: [],
  by_model: [],
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer', effective?: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({
    token: 'valid-token',
    expiresAt: Date.now() + 60_000,
    memberships: [{ company_id: 4, human_role: role, effective_role: effective ?? role }],
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <Usage />
    </QueryClientProvider>,
  )
}

describe('Usage', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('stays closed to a viewer without asking the backend', () => {
    const fetch = vi.fn(async () => response(200, report))
    vi.stubGlobal('fetch', fetch)
    renderAs('Viewer')
    expect(screen.getByRole('status')).toHaveTextContent('Only managers and owners read the usage report.')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('opens to a viewer a team makes a manager, and never shows an unknown cost as zero', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(200, report)))
    renderAs('Viewer', 'Manager')
    expect(await screen.findByText('$2.00')).toBeInTheDocument()
    expect(screen.getByText('+ 3 turns at unknown cost')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Person' }))
    const table = screen.getByRole('table')
    const unattributed = within(table).getByText('Unattributed').closest('tr') as HTMLElement
    expect(within(unattributed).getByText('unknown')).toBeInTheDocument()
    expect(within(unattributed).queryByText('$0.00')).toBeNull()
    expect(within(table).getByText('Ana')).toBeInTheDocument()
  })

  it('exports the CSV through the API with the range and period', async () => {
    const fetch = vi.fn(async (url: string) =>
      url.includes('usage.csv') ? response(200, '# La Fabrique usage report\r\n') : response(200, report))
    vi.stubGlobal('fetch', fetch)
    const createObjectURL = vi.fn(() => 'blob:usage')
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    renderAs('Manager')
    await screen.findByText('$2.00')

    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-11-02' } })
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-11-14' } })
    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }))

    await vi.waitFor(() => expect(click).toHaveBeenCalled())
    const csvCall = fetch.mock.calls.map(c => String(c[0])).find(u => u.includes('usage.csv'))!
    expect(csvCall).toContain('/v1/companies/4/usage.csv?since=2026-11-02&until=2026-11-14&period=week')
    expect(createObjectURL).toHaveBeenCalled()
  })
})
