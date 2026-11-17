import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CompanyFindingDetail from './CompanyFindingDetail'
import CompanyFindings from './CompanyFindings'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const B = 'a'.repeat(40)
const C = 'c0ffee1234567890c0ffee1234567890c0ffee12'

const REVERTED = {
  id: 7,
  company_id: 4,
  source: 'retrospective',
  source_ref: 'docs/retrospectives/2026-11-10.md#F3',
  title: 'The worker re-reads the whole board every step',
  specification_sha256: 'f'.repeat(64),
  specification_path: 'scripts/self_host/specs/board-reads.json',
  target_measure: 'pytest backend/tests/test_board_reads.py',
  status: 'reverted',
  outcomes: [{
    cycle: 1,
    decision: 'revert',
    reasons: [
      { guard: 'bench', outcome: 'fail', code: 'regression', detail: 'tokens rose 61% over the median' },
      'the target measure still fails on C',
    ],
    baseline_sha: B,
    candidate_sha: C,
    date: '2026-11-12T09:30:00+00:00',
    produced_by: 'platform',
  }],
  closed_by: null,
  created_at: '2026-11-10T08:00:00+00:00',
  updated_at: '2026-11-12T09:30:00+00:00',
}

const OPEN = {
  ...REVERTED,
  id: 8,
  source: null,
  source_ref: null,
  title: 'Carry-over loses the task notes',
  specification_sha256: 'e'.repeat(64),
  target_measure: null,
  status: 'open',
  outcomes: [],
}

function stubApi(handler: (path: string) => Response) {
  const fetch = vi.fn(async (url: string) => handler(new URL(url).pathname + new URL(url).search))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderAt(path: string, role: 'Owner' | 'Manager' | 'Viewer' = 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/company/findings" element={<CompanyFindings />} />
          <Route path="/company/findings/:id" element={<CompanyFindingDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('CompanyFindings', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('lists the company\'s findings with their source, status as text and target measure', async () => {
    const fetch = stubApi(path => path === '/v1/findings?company_id=4'
      ? response(200, { findings: [OPEN, REVERTED] }) : response(404))
    renderAt('/company/findings')
    const open = await screen.findByRole('article', { name: 'Carry-over loses the task notes' })
    expect(within(open).getByText('Open')).toBeInTheDocument()
    expect(within(open).getByText('Not recorded')).toBeInTheDocument()
    expect(within(open).getByText('None yet: no cycle starts until a person writes one.')).toBeInTheDocument()
    expect(within(open).getByText('No cycle has run for this finding yet.')).toBeInTheDocument()
    expect(within(open).getByRole('link', { name: 'Carry-over loses the task notes' })).toHaveAttribute('href', '/company/findings/8')
    const reverted = screen.getByRole('article', { name: REVERTED.title })
    expect(within(reverted).getByText(/^retrospective/)).toBeInTheDocument()
    expect(within(reverted).getByText(REVERTED.source_ref)).toBeInTheDocument()
    expect(within(reverted).getByText('pytest backend/tests/test_board_reads.py')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('shows a reverted finding\'s cycle: decision, reasons, B and C shas and date', async () => {
    stubApi(() => response(200, { findings: [REVERTED] }))
    renderAt('/company/findings')
    const card = await screen.findByRole('article', { name: REVERTED.title })
    expect(within(card).getByText('Reverted')).toBeInTheDocument()
    const table = within(card).getByRole('table', { name: `Cycles for ${REVERTED.title}` })
    const row = within(table).getAllByRole('row')[1]
    const cells = within(row).getAllByRole('cell')
    expect(cells[0]).toHaveTextContent('1')
    expect(cells[1]).toHaveTextContent('Revert')
    expect(cells[2]).toHaveTextContent('bench fail — tokens rose 61% over the median')
    expect(cells[2]).toHaveTextContent('the target measure still fails on C')
    expect(within(cells[3]).getByText(B.slice(0, 12))).toHaveAttribute('title', B)
    expect(within(cells[4]).getByText(C.slice(0, 12))).toHaveAttribute('title', C)
    expect(cells[5].textContent).toMatch(/2026/)
  })

  it('says so when the company has no findings', async () => {
    stubApi(() => response(200, { findings: [] }))
    renderAt('/company/findings')
    expect(await screen.findByText(/No findings yet/)).toBeInTheDocument()
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
  })

  it('shows the error and retries on request', async () => {
    const fetch = stubApi(() => response(403, { detail: 'Not a member of this company' }))
    renderAt('/company/findings')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not load the findings: Not a member of this company')
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
  })

  it('offers a viewer no action: the page is read-only', async () => {
    stubApi(() => response(200, { findings: [REVERTED, OPEN] }))
    renderAt('/company/findings', 'Viewer')
    await screen.findByRole('article', { name: REVERTED.title })
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    expect(screen.queryAllByRole('textbox')).toHaveLength(0)
  })
})

describe('CompanyFindingDetail', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows the record, every cycle and the specification text, with no action', async () => {
    stubApi(path => path === '/v1/findings/7'
      ? response(200, { ...REVERTED, specification_text: '{"title": "Board reads"}' }) : response(404))
    renderAt('/company/findings/7', 'Viewer')
    expect(await screen.findByRole('heading', { level: 1, name: REVERTED.title })).toBeInTheDocument()
    expect(screen.getByText('Reverted')).toBeInTheDocument()
    expect(screen.getByText('scripts/self_host/specs/board-reads.json')).toBeInTheDocument()
    expect(screen.getByText('f'.repeat(64))).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Cycles' })).toHaveTextContent('Revert')
    expect(screen.getByText('{"title": "Board reads"}')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Findings' })).toHaveAttribute('href', '/company/findings')
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('says a finding of another company or none is not found', async () => {
    stubApi(() => response(404, { detail: 'Finding not found' }))
    renderAt('/company/findings/99')
    expect(await screen.findByRole('alert')).toHaveTextContent('This finding does not exist, or it belongs to another company.')
  })
})
