import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import KanbanBoard from './KanbanBoard'
import { useCurrentRole, useSprintBoard, useUpdateBacklogItem } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'

// The review of the board redesign (PR #633): declared protected paths are a
// claim, the board's snapshot decides the verdict, focus follows a keyboard
// move, and an accepted protected delivery no longer reads as held.
vi.mock('../hooks/useQueries', async importOriginal => ({
  ...(await importOriginal<typeof import('../hooks/useQueries')>()),
  useSprintBoard: vi.fn(),
  useUpdateBacklogItem: vi.fn(),
  useCurrentRole: vi.fn(),
}))

const NOTHING = { run_id: null, verdict: null, risk_tier: null, risk_areas: [], protected_paths: [], waits_for_person: false }

function makeItem(id: number, title: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    id, sprint_id: 7, title, description: '', status, priority: 'high', assignee: 'Developer', comments: [],
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', observed: NOTHING,
    reported: { protected_paths: [] }, ...extra,
  }
}

function run(id: number, externalRef: string, version: number, status: string) {
  return {
    id, company_id: 42, external_ref: externalRef, version, producer: 'le-fabrique-task-worker', model: null,
    agent_role: 'Developer', subject: { sprint_id: 7 }, claims: null, artifact_digest: 'd', artifact_size: 1, status,
    started_at: null, finished_at: null, ingested_at: '2026-10-01T00:00:00Z', audit_log_id: null, latest_approval: null,
  }
}

const mutate = vi.fn()

function setBoard(columns: Record<string, unknown[]>) {
  vi.mocked(useSprintBoard).mockReturnValue({
    data: {
      sprint: { id: 7, sprint_name: 'Hardening' },
      columns: { todo: [], in_progress: [], in_review: [], done: [], blocked: [], ...columns },
      total: Object.values(columns).flat().length,
    },
    isLoading: false, isError: false, refetch: vi.fn(),
  } as never)
}

function stubRuns(runs: unknown[]) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true, status: 200, headers: new Headers(), json: () => Promise.resolve({ agent_runs: runs }),
  }))
}

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } })

function renderBoard() {
  const queries = client()
  const tree = () => (
    <QueryClientProvider client={queries}>
      <MemoryRouter><KanbanBoard sprintId="7" /></MemoryRouter>
    </QueryClientProvider>
  )
  const result = render(tree())
  return { ...result, rerenderBoard: () => result.rerender(tree()) }
}

function cardOf(title: string) {
  return screen.getByRole('link', { name: title }).closest('.card') as HTMLElement
}

describe('The board after review', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    useAppStore.setState({ companyId: 42 })
    vi.mocked(useUpdateBacklogItem).mockReturnValue({ mutate, isPending: false } as never)
    vi.mocked(useCurrentRole).mockReturnValue('Viewer')
    stubRuns([])
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reports a protected path only the producer declared, and still holds the task for a person', () => {
    setBoard({
      blocked: [makeItem(2, 'Tune the grader', 'blocked', {
        observed: { ...NOTHING, risk_tier: 'high', waits_for_person: true },
        reported: { protected_paths: ['bench/a.json'] },
      })],
    })
    renderBoard()
    const card = cardOf('Tune the grader')
    const observed = within(card).getByRole('group', { name: 'Observed' })
    const reported = within(card).getByRole('group', { name: 'Reported' })
    expect(within(observed).queryByText('Held: protected path')).not.toBeInTheDocument()
    expect(within(observed).getByText('Needs a person')).toBeInTheDocument()
    expect(within(reported).getByText('Declared: protected path')).toBeInTheDocument()
    expect(within(reported).getByText(/bench\/a\.json/)).toBeInTheDocument()
  })

  it('takes the verdict from the board, and links the run only when it is the board\'s run', async () => {
    setBoard({
      in_review: [
        makeItem(2, 'Encrypt tokens', 'in_review', { observed: { ...NOTHING, run_id: 50, verdict: 'refuted' } }),
        makeItem(3, 'Rotate keys', 'in_review', { observed: { ...NOTHING, run_id: 60, verdict: 'verified' } }),
      ],
    })
    // The cached runs: task 2's is an older version (49), task 3's is the board's own (60).
    stubRuns([run(49, 'task-2', 1, 'verified'), run(60, 'task-3', 1, 'verified')])
    renderBoard()

    const current = await screen.findByRole('link', { name: 'Delivery: Verified' })
    expect(current).toHaveAttribute('href', '/evidence/60')
    expect(within(cardOf('Rotate keys')).getByRole('link', { name: 'Delivery: Verified' })).toBe(current)
    const stale = cardOf('Encrypt tokens')
    expect(within(stale).getByText('Refuted')).toBeInTheDocument()
    expect(within(stale).queryByText('Verified')).not.toBeInTheDocument()
    expect(within(stale).queryByRole('link', { name: /^Delivery:/ })).not.toBeInTheDocument()
  })

  it('does not read an accepted protected delivery as held', () => {
    const accepted = makeItem(4, 'Bench thresholds', 'done', {
      observed: { ...NOTHING, run_id: 7, verdict: 'verified', risk_tier: 'high', risk_areas: ['grader'],
        protected_paths: ['scripts/bench/grade.py'], waits_for_person: false },
    })
    setBoard({ done: [accepted], todo: [makeItem(5, 'Write docs', 'todo')] })
    renderBoard()
    const observed = within(cardOf('Bench thresholds')).getByRole('group', { name: 'Observed' })
    expect(within(observed).queryByText('Held: protected path')).not.toBeInTheDocument()
    expect(within(observed).queryByText('Needs a person')).not.toBeInTheDocument()
    expect(within(observed).getByText('Grader area')).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /needs? a person$/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Needs a person/ })).toHaveTextContent('0')
    fireEvent.click(screen.getByRole('button', { name: 'Collapse all' }))
    expect(screen.getByRole('link', { name: 'Task 4: Bench thresholds (verified)' })).toBeInTheDocument()
  })

  describe('focus after a keyboard move', () => {
    const before = makeItem(3, 'Write docs', 'todo')
    const after = makeItem(3, 'Write docs', 'in_progress')

    function moveRight() {
      mutate.mockImplementation((_vars, options) => options?.onSuccess?.({ ...after }))
      const view = renderBoard()
      const link = screen.getByRole('link', { name: 'Write docs' })
      link.focus()
      fireEvent.keyDown(link, { key: 'ArrowRight', altKey: true })
      return view
    }

    it('returns to the card in its new column once the board is refreshed', async () => {
      setBoard({ todo: [before] })
      const { rerenderBoard } = moveRight()
      setBoard({ in_progress: [after] })
      act(() => rerenderBoard())
      const moved = screen.getByRole('link', { name: 'Write docs' })
      expect(moved.closest('section')).toHaveAccessibleName('In Progress')
      await waitFor(() => expect(moved).toHaveFocus())
    })

    it('goes to its chip when the new column is folded', async () => {
      setBoard({ todo: [before] })
      const { rerenderBoard } = moveRight()
      fireEvent.click(screen.getByRole('button', { name: 'Collapse In Progress' }))
      setBoard({ in_progress: [after] })
      act(() => rerenderBoard())
      await waitFor(() => expect(screen.getByRole('link', { name: 'Task 3: Write docs' })).toHaveFocus())
    })

    it('goes to the new column when a filter now hides the card', async () => {
      setBoard({ todo: [before, makeItem(5, 'Waits', 'todo', { waiting_for: [3] })] })
      const { rerenderBoard } = moveRight()
      fireEvent.click(screen.getByRole('button', { name: /^Waiting on tasks/ }))
      setBoard({ in_progress: [{ ...after }], todo: [makeItem(5, 'Waits', 'todo', { waiting_for: [3] })] })
      act(() => rerenderBoard())
      expect(screen.queryByRole('link', { name: 'Write docs' })).not.toBeInTheDocument()
      await waitFor(() => expect(screen.getByRole('button', { name: 'Collapse In Progress' })).toHaveFocus())
    })
  })
})
