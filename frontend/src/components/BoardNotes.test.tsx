import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BoardNotes } from './BoardNotes'
import { queryClient } from '../lib/queryClient'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const links = (files: string[] = [], requirements: string[] = []) => ({ files, symbols: [], requirements, modules: [] })

const NOTES = [
  {
    id: 12, sprint_id: 8, kind: 'decision', text: 'Timestamps are stored in UTC.', author_role: 'Developer',
    author_name: 'Dev', author_task_id: 31, run_id: 44, links: links(['src/store.py', 'src/api.py'], ['REQ-1.2']),
    supersedes_id: 9, status: 'active', status_by: null, status_at: null, status_reason: null,
    created_at: '2026-11-15T10:00:00Z',
    observed: { files: { 'src/store.py': { run_id: 44, backlog_item_id: 31 }, 'src/api.py': null } },
  },
  {
    id: 9, sprint_id: 8, kind: 'gotcha', text: 'Use local time.', author_role: 'person',
    author_name: 'lead@example.com', author_task_id: null, run_id: null, links: links(),
    supersedes_id: null, status: 'marked_wrong', status_by: 'lead@example.com', status_at: '2026-11-15T11:00:00Z',
    status_reason: 'We agreed on UTC', created_at: '2026-11-15T09:00:00Z', observed: { files: {} },
  },
]
const BODY = {
  notes: NOTES,
  kinds: ['decision', 'interface_change', 'schema_change', 'gotcha', 'question'],
  metrics: {
    notes: { total: 2, active: 1, superseded: 0, marked_wrong: 1, by_kind: {} },
    rework: 3,
    contradiction_rework: 1,
  },
}

function renderBoard(canManage: boolean) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <BoardNotes sprintId={8} canManage={canManage} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('BoardNotes', () => {
  beforeEach(() => {
    queryClient.clear()
    useAuthStore.setState({ expiresAt: Date.now() + 60_000 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows the active notes as claims with what was observed beside each file', async () => {
    const fetch = vi.fn().mockResolvedValue(response(200, BODY))
    vi.stubGlobal('fetch', fetch)
    renderBoard(false)

    const items = await screen.findAllByRole('listitem', { name: undefined })
    const note = items.find(item => item.textContent?.includes('Timestamps are stored in UTC.'))!
    expect(note).toHaveTextContent('Decision')
    expect(note).toHaveTextContent('Developer')
    expect(within(note).getByRole('link', { name: 'task #31' })).toHaveAttribute('href', '/sprints/8/tasks/31')
    expect(note).toHaveTextContent('src/store.pyobserved: changed by verified run #44')
    expect(note).toHaveTextContent('src/api.pynot observed')
    expect(note).toHaveTextContent('Requirements: REQ-1.2')
    expect(note).toHaveTextContent('Supersedes note #9')
    expect(screen.queryByText('Use local time.')).not.toBeInTheDocument()
    expect(screen.getByTestId('board-notes-metrics')).toHaveTextContent(
      '1 active · 1 marked wrong · rework for a contradiction with another task: 1 of 3',
    )
    // A Viewer reads: no form, no marking.
    expect(screen.queryByRole('form', { name: 'Add a note' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Mark wrong' })).not.toBeInTheDocument()
    expect(new URL(fetch.mock.calls[0][0]).pathname).toBe('/v1/sprints/8/board_notes')

    fireEvent.click(screen.getByRole('checkbox', { name: 'Show superseded and marked wrong' }))
    const wrong = (await screen.findByText('Use local time.')).closest('li')!
    expect(wrong).toHaveTextContent('Marked wrong by lead@example.com — We agreed on UTC')
    expect(wrong).toHaveTextContent('lead@example.com')
  })

  it('lets a Manager add a note and mark one wrong', async () => {
    const fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'POST') return Promise.resolve(response(url.includes('mark_wrong') ? 200 : 201, NOTES[0]))
      return Promise.resolve(response(200, BODY))
    })
    vi.stubGlobal('fetch', fetch)
    renderBoard(true)

    const form = await screen.findByRole('form', { name: 'Add a note' })
    fireEvent.change(within(form).getByRole('combobox', { name: 'Kind' }), { target: { value: 'gotcha' } })
    fireEvent.change(within(form).getByRole('textbox', { name: 'Files' }), { target: { value: 'src/a.py, src/b.py' } })
    fireEvent.change(within(form).getByRole('textbox', { name: 'Note' }), { target: { value: 'The cache is per process.' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Add note' }))
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true))
    const [addUrl, addInit] = fetch.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(new URL(addUrl).pathname).toBe('/v1/sprints/8/board_notes')
    expect(JSON.parse(addInit.body)).toEqual({
      kind: 'gotcha', text: 'The cache is per process.', files: ['src/a.py', 'src/b.py'], requirements: [],
    })

    fireEvent.click(await screen.findByRole('button', { name: 'Mark wrong' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Why it is wrong (optional)' }), { target: { value: 'Not agreed' } })
    fireEvent.click(screen.getByRole('button', { name: 'Mark as wrong' }))
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => String(url).includes('/mark_wrong'))).toBe(true))
    const [markUrl, markInit] = fetch.mock.calls.find(([url]) => String(url).includes('/mark_wrong'))!
    expect(new URL(markUrl).pathname).toBe('/v1/board_notes/12/mark_wrong')
    expect(JSON.parse(markInit.body)).toEqual({ reason: 'Not agreed' })
  })

  it('says when there is nothing yet, and offers a retry when loading fails', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(response(403, { detail: 'Not a member of this company' }))
      .mockResolvedValueOnce(response(200, { ...BODY, notes: [] }))
    vi.stubGlobal('fetch', fetch)
    renderBoard(false)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not load the board notes.')
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/No notes yet/)).toBeInTheDocument()
  })
})
