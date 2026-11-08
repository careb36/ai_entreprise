import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ArchitectureDetail from './ArchitectureDetail'
import { APPROVED_ARCHITECTURE, DRAFT_ARCHITECTURE } from '../test/architectureFixtures'
import { APPROVED } from '../test/specificationFixtures'
import { queryClient } from '../lib/queryClient'
import type { Architecture } from '../services/architectures'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return {
    ok: status >= 200 && status < 300, status,
    json: async () => body,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
  } as Response
}

function stubApi(architecture: Architecture, overrides: Record<string, () => Response> = {}) {
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const { pathname } = new URL(url)
    const key = `${init.method ?? 'GET'} ${pathname}`
    if (overrides[key]) return overrides[key]()
    if (key === `GET /v1/specifications/${APPROVED.id}`) return response(200, APPROVED)
    if (key === `GET /v1/architectures/${architecture.id}`) return response(200, architecture)
    return response(404, { detail: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer', id = DRAFT_ARCHITECTURE.id) {
  useAuthStore.setState({ token: 'valid-token', expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/company/specifications/3/architectures/${id}`]}>
        <Routes>
          <Route path="/company/specifications/:specId/architectures/:id" element={<ArchitectureDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function posted(fetch: ReturnType<typeof vi.fn>, path: string) {
  return fetch.mock.calls.find(([url, init]) => new URL(url).pathname === path && init?.method === 'POST')
}

describe('ArchitectureDetail', () => {
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
    stubApi(DRAFT_ARCHITECTURE)
    renderAs('Manager')

    expect(await screen.findByRole('heading', { level: 1, name: 'Login design' })).toBeInTheDocument()
    expect(screen.getByText('Draft')).toBeInTheDocument()
    expect(screen.getByText('Drafted by Architect')).toBeInTheDocument()
    const check = screen.getByRole('region', { name: 'Check' })
    const blocking = within(check).getByRole('list', { name: 'Blocking' })
    const warnings = within(check).getByRole('list', { name: 'Warnings' })
    expect(blocking).toHaveTextContent('Q1')
    expect(blocking).toHaveTextContent('The question is unanswered')
    expect(warnings).toHaveTextContent('MOD-2')
    expect(warnings).toHaveTextContent('The module covers no requirement')
    expect(blocking.compareDocumentPosition(warnings) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    const approve = screen.getByRole('button', { name: 'Approve' })
    expect(approve).toBeDisabled()
    expect(approve).toHaveAttribute('title', 'Fix the blocking findings first.')
    // The modules with their path, dependencies and coverage; the interface with its contract; the decision and the convention.
    const web = screen.getByRole('listitem', { name: 'MOD-2' })
    expect(web).toHaveTextContent('Web')
    expect(web).toHaveTextContent('src/web')
    expect(web).toHaveTextContent('Depends on')
    expect(web).toHaveTextContent('MOD-1')
    const sessions = screen.getByRole('listitem', { name: 'MOD-1' })
    expect(within(sessions).getByRole('link', { name: 'REQ-1' })).toHaveAttribute('href', '/company/specifications/3')
    const api = screen.getByRole('listitem', { name: 'IF-1' })
    expect(api).toHaveTextContent('Session API')
    expect(api).toHaveTextContent('HTTP')
    expect(api).toHaveTextContent('Provided by')
    expect(api).toHaveTextContent('Consumed by')
    expect(within(api).getByText(/POST \/login/)).toHaveTextContent('DELETE /login')
    expect(screen.getByRole('listitem', { name: 'ADR-1' })).toHaveTextContent('Stateless tokens')
    expect(screen.getByText('Tests beside the code')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Write to the repository' })).not.toBeInTheDocument()
  })

  it('posts the answer to a question and shows what was stored', async () => {
    const fetch = stubApi(DRAFT_ARCHITECTURE, {
      'POST /v1/architectures/21/questions/0': () => response(200, { ...DRAFT_ARCHITECTURE, questions: [{ question: 'Which token lifetime?', answer: 'One day' }] }),
    })
    renderAs('Manager')

    const questions = await screen.findByRole('region', { name: 'Open questions' })
    expect(questions).toHaveTextContent('Unanswered')
    fireEvent.click(within(questions).getByRole('button', { name: 'Answer' }))
    fireEvent.change(within(questions).getByRole('textbox', { name: 'Answer to question 1' }), { target: { value: ' One day ' } })
    fireEvent.click(within(questions).getByRole('button', { name: 'Save answer' }))

    expect(await within(questions).findByText('Answer: One day')).toBeInTheDocument()
    expect(JSON.parse(String(posted(fetch, '/v1/architectures/21/questions/0')![1]!.body))).toEqual({ answer: 'One day' })
  })

  it('shows the findings of a refused approval', async () => {
    const ready = { ...DRAFT_ARCHITECTURE, questions: [], check: { ok: true, blocking: 0, warnings: 0, findings: [] } }
    stubApi(ready, {
      'POST /v1/architectures/21/approve': () => response(409, {
        detail: { message: 'The check blocks the approval', findings: [{ severity: 'blocking', where: 'IF-1', code: 'no_consumer', message: 'Nobody consumes it' }] },
      }),
    })
    renderAs('Manager')

    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }))
    const actions = screen.getByRole('region', { name: 'Actions' })
    expect(await within(actions).findByRole('alert')).toHaveTextContent('Could not approve it: The check blocks the approval')
    expect(within(actions).getByRole('list', { name: 'Blocking' })).toHaveTextContent('Nobody consumes it')
  })

  it('writes an approved architecture to the repository and shows the path and hash', async () => {
    const written = { ...APPROVED_ARCHITECTURE, written_path: 'docs/ARCHITECTURE.md', written_sha256: 'abcdef0123456789deadbeef', written_at: '2026-10-04T12:00:00Z' }
    const fetch = stubApi(APPROVED_ARCHITECTURE, { 'POST /v1/architectures/22/write': () => response(200, written) })
    renderAs('Owner', 22)

    expect(await screen.findByText('Approved by ana@acme.test on Oct 3, 2026')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Revision of #21' })).toHaveAttribute('href', '/company/specifications/3/architectures/21')
    fireEvent.click(screen.getByRole('button', { name: 'Write to the repository' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Written to docs/ARCHITECTURE.md, sha256 abcdef012345')
    expect(posted(fetch, '/v1/architectures/22/write')).toBeDefined()
    expect(screen.getByText('Written to docs/ARCHITECTURE.md on Oct 4, 2026, sha256 abcdef012345')).toBeInTheDocument()
  })

  it('says the workspace daemon is not running when the write gets a 503, and shows other refusals', async () => {
    const fetch = stubApi(APPROVED_ARCHITECTURE, { 'POST /v1/architectures/22/write': () => response(503, { detail: 'No workspace daemon answered' }) })
    renderAs('Manager', 22)

    fireEvent.click(await screen.findByRole('button', { name: 'Write to the repository' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The workspace daemon is not connected.')
    expect(alert).toHaveTextContent('No workspace daemon answered')
    expect(alert).toHaveTextContent('start-daemon')
    // Sent once: a 503 that means "not connected" is not retried.
    expect(fetch.mock.calls.filter(([url]) => new URL(url).pathname === '/v1/architectures/22/write')).toHaveLength(1)

    fetch.mockImplementationOnce(async () => response(409, { detail: 'The company has no project folder' }))
    fireEvent.click(screen.getByRole('button', { name: 'Write to the repository' }))
    expect(await screen.findByText('Could not write it: The company has no project folder')).toBeInTheDocument()
  })

  it('opens the new draft a revision creates', async () => {
    const revised = { ...APPROVED_ARCHITECTURE, id: 23, status: 'draft', version: 3, supersedes_id: 22 }
    const fetch = stubApi(APPROVED_ARCHITECTURE, {
      'POST /v1/architectures/22/revise': () => response(201, revised),
      'GET /v1/architectures/23': () => response(200, revised),
    })
    renderAs('Manager', 22)

    fireEvent.click(await screen.findByRole('button', { name: 'Revise' }))
    expect(await screen.findByText('v3')).toBeInTheDocument()
    expect(posted(fetch, '/v1/architectures/22/revise')).toBeDefined()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
  })

  it('opens the revision already in draft when the backend names it', async () => {
    const existing = { ...APPROVED_ARCHITECTURE, id: 24, status: 'draft', version: 3, supersedes_id: 22 }
    stubApi(APPROVED_ARCHITECTURE, {
      'POST /v1/architectures/22/revise': () => response(409, { detail: { message: 'A revision is already in draft', architecture_id: 24 } }),
      'GET /v1/architectures/24': () => response(200, existing),
    })
    renderAs('Manager', 22)

    fireEvent.click(await screen.findByRole('button', { name: 'Revise' }))
    expect(await screen.findByText('v3')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('asks the Architect to revise a draft from its answers', async () => {
    const fetch = stubApi(DRAFT_ARCHITECTURE, { 'POST /v1/architectures/draft': () => response(200, { ...DRAFT_ARCHITECTURE, overview: 'Revised.' }) })
    renderAs('Manager')

    fireEvent.click(await screen.findByRole('button', { name: 'Ask the Architect to revise' }))
    expect(await screen.findByText('Revised.')).toBeInTheDocument()
    expect(JSON.parse(String(posted(fetch, '/v1/architectures/draft')![1]!.body))).toEqual({ specification_id: 3, architecture_id: 21 })
  })

  it('fetches the Markdown document with the token and shows it', async () => {
    const fetch = stubApi(APPROVED_ARCHITECTURE, { 'GET /v1/architectures/22/document': () => response(200, '# Login design\n\nA session service.') })
    renderAs('Viewer', 22)

    fireEvent.click(await screen.findByRole('button', { name: 'View as Markdown' }))
    expect(await screen.findByText(/# Login design/)).toBeInTheDocument()
    const call = fetch.mock.calls.find(([url]) => new URL(url).pathname === '/v1/architectures/22/document')!
    expect(new Headers(call[1]!.headers).get('Authorization')).toBe('Bearer valid-token')
  })

  it('reads only, for a Viewer', async () => {
    stubApi(DRAFT_ARCHITECTURE)
    renderAs('Viewer')
    expect(await screen.findByRole('heading', { level: 1, name: 'Login design' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Actions' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Answer' })).not.toBeInTheDocument()
  })

  it('edits a draft inline and saves the body', async () => {
    const fetch = stubApi(DRAFT_ARCHITECTURE, { 'PUT /v1/architectures/21': () => response(200, { ...DRAFT_ARCHITECTURE, title: 'Login design v1b' }) })
    renderAs('Manager')

    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }))
    const form = screen.getByRole('form', { name: 'Architecture editor' })
    fireEvent.change(within(form).getByRole('textbox', { name: 'Title' }), { target: { value: 'Login design v1b' } })
    expect(within(form).getByRole('checkbox', { name: 'Module 2 depends on Sessions' })).toBeChecked()
    expect(within(form).getByRole('checkbox', { name: 'Module 1 covers REQ-1' })).toBeChecked()
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Login design v1b' })).toBeInTheDocument()
    const call = fetch.mock.calls.find(([url, init]) => new URL(url).pathname === '/v1/architectures/21' && init?.method === 'PUT')!
    const body = JSON.parse(String(call[1]!.body))
    expect(body.title).toBe('Login design v1b')
    expect(body.modules[1]).toEqual({ name: 'Web', responsibility: 'The HTTP layer.', path: 'src/web', depends_on: ['Sessions'], covers: [] })
    expect(body.interfaces[0]).toEqual({ name: 'Session API', kind: 'http', module: 'Sessions', contract: 'POST /login\nDELETE /login', consumers: ['Web'] })
  })
})
