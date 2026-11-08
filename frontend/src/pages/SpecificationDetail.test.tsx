import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SpecificationDetail from './SpecificationDetail'
import { DRAFT_ARCHITECTURE } from '../test/architectureFixtures'
import { APPROVED, DRAFT } from '../test/specificationFixtures'
import { queryClient } from '../lib/queryClient'
import type { Specification } from '../services/specifications'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const READY: Specification = {
  ...DRAFT, questions: [{ question: 'Which currencies?', answer: 'USD only' }],
  check: { ok: true, blocking: 0, warnings: 1, findings: [{ severity: 'warning', where: 'NFR-1', code: 'vague_target', message: 'The target has no number' }] },
  coverage: {
    criteria: [{
      id: 'REQ-1.1', requirement_id: 'REQ-1', status: 'done',
      tasks: [{ id: 12, sprint_id: 9, title: 'Cart listing', status: 'done' }],
      tests: [{ id: 11, sprint_id: 9, title: 'Acceptance tests for REQ-1', status: 'done' }],
      test: 'test_req_1_1_…', verified: true, last_result: 'pass',
    }],
    total: 1, covered: 1, done: 1, verified: 1,
  },
}

function stubApi(specification: Specification, overrides: Record<string, () => Response> = {}) {
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const { pathname } = new URL(url)
    const key = `${init.method ?? 'GET'} ${pathname}`
    if (overrides[key]) return overrides[key]()
    if (key === `GET /v1/specifications/${specification.id}`) return response(200, specification)
    if (key === 'GET /v1/architectures') return response(200, { architectures: [] })
    return response(404, { detail: 'Specification not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer', id = 5) {
  useAuthStore.setState({ token: 'valid-token', expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/company/specifications/${id}`]}>
        <Routes>
          <Route path="/company/specifications/:id" element={<SpecificationDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function posted(fetch: ReturnType<typeof vi.fn>, path: string) {
  return fetch.mock.calls.find(([url, init]) => new URL(url).pathname === path && init?.method === 'POST')
}

describe('SpecificationDetail', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows the blocking findings before the warnings, each with where it is, and keeps Approve disabled', async () => {
    stubApi(DRAFT)
    renderAs('Manager')

    expect(await screen.findByRole('heading', { level: 1, name: 'Checkout' })).toBeInTheDocument()
    expect(screen.getByText('Draft')).toBeInTheDocument()
    expect(screen.getByText('Drafted by PO')).toBeInTheDocument()
    const check = screen.getByRole('region', { name: 'Check' })
    const blocking = within(check).getByRole('list', { name: 'Blocking' })
    const warnings = within(check).getByRole('list', { name: 'Warnings' })
    expect(blocking).toHaveTextContent('Q1')
    expect(blocking).toHaveTextContent('The question is unanswered')
    expect(warnings).toHaveTextContent('NFR-1')
    expect(warnings).toHaveTextContent('The target has no number')
    expect(blocking.compareDocumentPosition(warnings) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(within(check).queryByText(/ready to approve/)).not.toBeInTheDocument()

    const approve = screen.getByRole('button', { name: 'Approve' })
    expect(approve).toBeDisabled()
    expect(approve).toHaveAttribute('title', 'Fix the blocking findings first.')
    // The requirement, its criterion, the quality attribute and the constraint are all shown.
    const criteria = screen.getByRole('list', { name: 'Acceptance criteria of REQ-1' })
    expect(criteria).toHaveTextContent('REQ-1.1')
    expect(criteria).toHaveTextContent(/Given a cart · When an item is added · Then it is listed/)
    expect(screen.getByText('Latency')).toBeInTheDocument()
    expect(screen.getByText('Postgres only')).toBeInTheDocument()
  })

  it('posts the answer to a question and shows what was stored', async () => {
    const fetch = stubApi(DRAFT, {
      'POST /v1/specifications/5/questions/0': () => response(200, { ...DRAFT, questions: [{ question: 'Which currencies?', answer: 'USD only' }] }),
    })
    renderAs('Manager')

    const questions = await screen.findByRole('region', { name: 'Open questions' })
    expect(questions).toHaveTextContent('Unanswered')
    fireEvent.click(within(questions).getByRole('button', { name: 'Answer' }))
    fireEvent.change(within(questions).getByRole('textbox', { name: 'Answer to question 1' }), { target: { value: ' USD only ' } })
    fireEvent.click(within(questions).getByRole('button', { name: 'Save answer' }))

    expect(await within(questions).findByText('Answer: USD only')).toBeInTheDocument()
    expect(JSON.parse(String(posted(fetch, '/v1/specifications/5/questions/0')![1]!.body))).toEqual({ answer: 'USD only' })
    expect(within(questions).getByRole('button', { name: 'Edit answer' })).toBeInTheDocument()
  })

  it('shows coverage beside each criterion, with links to its tasks, and approves a ready draft', async () => {
    const fetch = stubApi(READY, { 'POST /v1/specifications/5/approve': () => response(200, { ...READY, status: 'approved', approved_by: 'ana@acme.test', approved_at: '2026-10-02T12:00:00Z' }) })
    renderAs('Owner')

    expect(await screen.findByText('1 of 1 criteria covered, 1 done · 1 of 1 verified by a passing test')).toBeInTheDocument()
    const criteria = screen.getByRole('list', { name: 'Acceptance criteria of REQ-1' })
    expect(within(criteria).getByText('Done')).toBeInTheDocument()
    expect(within(criteria).getByRole('link', { name: 'task #12' })).toHaveAttribute('href', '/sprints/9/tasks/12')
    // The criterion's acceptance test passed in an observed run; the test-writing task is linked.
    expect(within(criteria).getByText('Verified')).toHaveAttribute('title', 'test_req_1_1_…')
    expect(within(criteria).getByRole('link', { name: 'tests #11' })).toHaveAttribute('href', '/sprints/9/tasks/11')
    expect(screen.getByRole('region', { name: 'Check' })).toHaveTextContent('Nothing blocks this specification: it is ready to approve.')

    const approve = screen.getByRole('button', { name: 'Approve' })
    expect(approve).toBeEnabled()
    fireEvent.click(approve)
    expect(await screen.findByText('Approved')).toBeInTheDocument()
    expect(posted(fetch, '/v1/specifications/5/approve')).toBeDefined()
    expect(screen.getByRole('button', { name: 'Revise' })).toBeInTheDocument()
  })

  it('shows a failing or missing acceptance test as not verified', async () => {
    const criterion = READY.coverage!.criteria[0]
    stubApi({
      ...READY,
      coverage: {
        ...READY.coverage!,
        verified: 0,
        criteria: [{ ...criterion, verified: false, last_result: 'fail' }],
      },
    })
    renderAs('Viewer')
    expect(await screen.findByText('1 of 1 criteria covered, 1 done · 0 of 1 verified by a passing test')).toBeInTheDocument()
    expect(screen.getByText('Test failing')).toBeInTheDocument()
    expect(screen.queryByText('Verified')).not.toBeInTheDocument()
  })

  it('shows the findings of a refused approval', async () => {
    stubApi(READY, {
      'POST /v1/specifications/5/approve': () => response(409, {
        detail: { message: 'The check blocks the approval', findings: [{ severity: 'blocking', where: 'REQ-1.1', code: 'empty', message: 'Then is empty' }] },
      }),
    })
    renderAs('Manager')

    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }))
    const actions = screen.getByRole('region', { name: 'Actions' })
    expect(await within(actions).findByRole('alert')).toHaveTextContent('Could not approve it: The check blocks the approval')
    expect(within(actions).getByRole('list', { name: 'Blocking' })).toHaveTextContent('Then is empty')
  })

  it('opens the new draft a revision creates', async () => {
    const revised = { ...APPROVED, id: 9, status: 'draft', version: 3, supersedes_id: 3 }
    const fetch = stubApi(APPROVED, {
      'POST /v1/specifications/3/revise': () => response(201, revised),
      'GET /v1/specifications/9': () => response(200, revised),
    })
    renderAs('Manager', 3)

    expect(await screen.findByText('Approved by ana@acme.test on Oct 2, 2026')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Revision of #2' })).toHaveAttribute('href', '/company/specifications/2')
    fireEvent.click(screen.getByRole('button', { name: 'Revise' }))
    expect(await screen.findByText('v3')).toBeInTheDocument()
    expect(posted(fetch, '/v1/specifications/3/revise')).toBeDefined()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
  })

  it('opens the revision already in draft when the backend names it', async () => {
    const existing = { ...APPROVED, id: 11, status: 'draft', version: 3, supersedes_id: 3 }
    stubApi(APPROVED, {
      'POST /v1/specifications/3/revise': () => response(409, { detail: { message: 'A revision is already in draft', specification_id: 11 } }),
      'GET /v1/specifications/11': () => response(200, existing),
    })
    renderAs('Manager', 3)

    fireEvent.click(await screen.findByRole('button', { name: 'Revise' }))
    expect(await screen.findByText('v3')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the architecture of an approved specification, and none for a draft', async () => {
    const fetch = stubApi(APPROVED, { 'GET /v1/architectures': () => response(200, { architectures: [DRAFT_ARCHITECTURE] }) })
    renderAs('Manager', 3)

    const section = await screen.findByRole('region', { name: 'Architecture' })
    expect(await within(section).findByRole('link', { name: 'Login design' })).toHaveAttribute('href', '/company/specifications/3/architectures/21')
    expect(section).toHaveTextContent('1 blocking · 1 warnings')
    expect(new URL(fetch.mock.calls.find(([url]) => new URL(url).pathname === '/v1/architectures')![0]).searchParams.get('specification_id')).toBe('3')
    expect(within(section).queryByRole('button', { name: 'Ask the Architect for a design' })).not.toBeInTheDocument()
  })

  it('reads only, for a Viewer', async () => {
    stubApi(READY)
    renderAs('Viewer')
    expect(await screen.findByRole('heading', { level: 1, name: 'Checkout' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Actions' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit answer' })).not.toBeInTheDocument()
    // A draft specification has no architecture section.
    expect(screen.queryByRole('region', { name: 'Architecture' })).not.toBeInTheDocument()
  })

  it('offers a retry when loading fails', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(response(404, { detail: 'Specification not found' }))
      .mockResolvedValue(response(200, DRAFT))
    vi.stubGlobal('fetch', fetch)
    renderAs('Viewer')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not load the specification: Specification not found')
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Checkout' })).toBeInTheDocument()
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
  })
})
