import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SelfImprovement from './SelfImprovement'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const state = {
  company_id: 4, rsi_enabled: true, self_hosting: true, stopped: true, consecutive_without_keep: 3, stop_after: 3,
  cycles_today: 1, cycles_per_day: 2, token_budget: 200000, budget_reason: null, max_diff_files: 10, max_diff_lines: 400,
  kept_merged_by_person: 1, person_merges_required: 3, may_start: false, why_not_start: 'stopped',
}
const cycle = {
  id: 9, cycle_key: 'rsi-2026-11-19-1', finding_id: 5, source: 'retrospective', status: 'awaiting_merge',
  baseline_sha: 'a'.repeat(40), candidate_sha: 'b'.repeat(40), sprint_id: null, integration_id: null, pull_request: null,
  decision: 'keep', reasons: [], diff_files: 2, diff_lines: 40, token_budget: 200000, tokens_reported: 1000,
  merged_sha: null, merged_by: null, started_at: null, ended_at: null, created_at: null,
}

function routed() {
  return vi.fn(async (url: string, _init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname === '/v1/companies/4/rsi/cycles') return response(200, { state, cycles: [cycle] })
    if (pathname === '/v1/companies/4/rsi/resume') return response(200, { ...state, stopped: false })
    if (pathname === '/v1/companies/4/rsi/cycles/9/merge') return response(200, { ...cycle, status: 'merged' })
    if (pathname === '/v1/companies/4/rsi/cycles/import') return response(200, cycle)
    return response(404)
  })
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(<QueryClientProvider client={queryClient}><MemoryRouter><SelfImprovement /></MemoryRouter></QueryClientProvider>)
}

const posts = (fetch: ReturnType<typeof routed>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === 'POST').map(([url, init]) => [new URL(String(url)).pathname, JSON.parse(String(init?.body))])

describe('SelfImprovement', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows a manager the state and the cycles, with no owner action', async () => {
    vi.stubGlobal('fetch', routed())
    renderAs('Manager')
    expect(await screen.findByText(/The loop stopped/)).toBeInTheDocument()
    const table = screen.getByRole('table', { name: 'Cycles' })
    expect(within(table).getByText('Kept, waiting for a merge')).toBeInTheDocument()
    expect(within(table).getByRole('link', { name: 'Finding 5' })).toHaveAttribute('href', '/company/findings/5')
    expect(screen.queryByRole('button', { name: 'Resume the loop' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Merge commit (full sha)')).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Import a cycle record' })).not.toBeInTheDocument()
  })

  it('lets an owner resume the loop and record a merge by its full sha', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    fireEvent.click(await screen.findByRole('button', { name: 'Resume the loop' }))
    await waitFor(() => expect(posts(fetch)).toContainEqual(['/v1/companies/4/rsi/resume', {}]))
    const sha = screen.getByLabelText('Merge commit (full sha)')
    fireEvent.change(sha, { target: { value: 'abc' } })
    expect(screen.getByRole('button', { name: 'Record' })).toBeDisabled()
    fireEvent.change(sha, { target: { value: 'C'.repeat(40) } })
    fireEvent.click(screen.getByRole('button', { name: 'Record' }))
    await waitFor(() => expect(posts(fetch)).toContainEqual(['/v1/companies/4/rsi/cycles/9/merge', { merged_sha: 'c'.repeat(40) }]))
  })

  it('imports a runner record pasted as JSON', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    const field = await screen.findByLabelText('The runner\'s record (JSON)')
    fireEvent.change(field, { target: { value: '[1, 2]' } })
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled()
    fireEvent.change(field, { target: { value: '{"schema": 1, "cycle_key": "k"}' } })
    fireEvent.click(screen.getByRole('button', { name: 'Import' }))
    expect(await screen.findByText('Record imported')).toBeInTheDocument()
    expect(posts(fetch)).toContainEqual(['/v1/companies/4/rsi/cycles/import', { record: { schema: 1, cycle_key: 'k' } }])
  })
})
