import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import KanbanBoard from './KanbanBoard'
import { useCurrentRole, useSprintBoard, useUpdateBacklogItem } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'

// The redesigned board (components/board/): what was observed apart from what
// agents reported, keyboard moves, folded columns, filters and presets, empty
// columns, compact cards and the activity panel.
vi.mock('../hooks/useQueries', async importOriginal => ({
  ...(await importOriginal<typeof import('../hooks/useQueries')>()),
  useSprintBoard: vi.fn(),
  useUpdateBacklogItem: vi.fn(),
  useCurrentRole: vi.fn(),
}))

const NOTHING = { verdict: null, risk_tier: null, risk_areas: [], protected_paths: [], waits_for_person: false }

function makeItem(id: number, title: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    id, sprint_id: 7, title, description: '', status, priority: 'high', assignee: 'Developer',
    comments: [], created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', observed: NOTHING, ...extra,
  }
}

const held = makeItem(2, 'Tune the grader', 'blocked', {
  comments: [{ author: 'QA', text: 'NEEDS WORK: thresholds too loose', created_at: '2026-10-01T01:00:00Z' }],
  observed: {
    verdict: 'verified', risk_tier: 'high', risk_areas: ['grader'],
    protected_paths: ['scripts/bench/grade.py', 'bench/x.json'], waits_for_person: true,
  },
})
const todo = makeItem(3, 'Write docs', 'todo', { assignee: 'TechWriter' })
const done = makeItem(4, 'Rotate keys', 'done', { observed: { ...NOTHING, verdict: 'verified', risk_tier: 'low' } })

const mutate = vi.fn()

function renderBoard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><KanbanBoard sprintId="7" /></MemoryRouter>
    </QueryClientProvider>,
  )
}

function cardOf(title: string) {
  return screen.getByRole('link', { name: title }).closest('.card') as HTMLElement
}

function notice() {
  return screen.queryAllByRole('status').find(element => !element.id.startsWith('DndLiveRegion')) ?? null
}

describe('The board redesign', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    useAppStore.setState({ companyId: 42 })
    vi.mocked(useSprintBoard).mockReturnValue({
      data: {
        sprint: { id: 7, sprint_name: 'Hardening' },
        columns: { todo: [todo], in_progress: [], in_review: [], done: [done], blocked: [held] },
        total: 3,
      },
      isLoading: false, isError: false, refetch: vi.fn(),
    } as never)
    vi.mocked(useUpdateBacklogItem).mockReturnValue({ mutate, isPending: false } as never)
    vi.mocked(useCurrentRole).mockReturnValue('Viewer')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, status: 200, headers: new Headers(), json: () => Promise.resolve({ agent_runs: [] }),
    }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('keeps what was observed apart from what agents reported', () => {
    renderBoard()
    const card = cardOf('Tune the grader')
    const observed = within(card).getByRole('group', { name: 'Observed' })
    const reported = within(card).getByRole('group', { name: 'Reported' })
    expect(within(observed).getByText('Verified')).toBeInTheDocument()
    expect(within(observed).getByText('Risk high')).toBeInTheDocument()
    expect(within(observed).getByText('Held: protected path')).toBeInTheDocument()
    expect(within(observed).getByText(/scripts\/bench\/grade\.py, bench\/x\.json/)).toBeInTheDocument()
    expect(within(observed).getByText('Grader area')).toBeInTheDocument()
    expect(within(observed).getByText('Needs a person')).toBeInTheDocument()
    // QA's rework request is a message: reported, never among the observations.
    expect(within(reported).getByText('QA rework 1/2')).toBeInTheDocument()
    expect(within(observed).queryByText('QA rework 1/2')).not.toBeInTheDocument()
    // A card with nothing observed and nothing reported has neither group.
    expect(within(cardOf('Write docs')).queryByRole('group', { name: 'Observed' })).not.toBeInTheDocument()
    expect(within(cardOf('Write docs')).queryByRole('group', { name: 'Reported' })).not.toBeInTheDocument()
    // The column says how many of its tasks need a person, in words.
    expect(screen.getByRole('img', { name: '1 task needs a person' })).toBeInTheDocument()
  })

  it('moves a focused card to the next or previous column with Alt and the arrows', () => {
    mutate.mockImplementation((_vars, options) => options?.onSuccess?.({ ...todo, status: 'in_progress' }))
    renderBoard()
    const link = screen.getByRole('link', { name: 'Write docs' })
    expect(link).toHaveAttribute('aria-keyshortcuts', 'Alt+ArrowLeft Alt+ArrowRight')
    fireEvent.keyDown(link, { key: 'ArrowRight', altKey: true })
    expect(mutate).toHaveBeenCalledWith({ itemId: 3, data: { status: 'in_progress' } }, expect.anything())
    expect(notice()).toHaveTextContent('"Write docs" moved to In Progress.')

    // To do is the first column: nothing to its left. An arrow without Alt does nothing.
    mutate.mockClear()
    fireEvent.keyDown(link, { key: 'ArrowLeft', altKey: true })
    fireEvent.keyDown(link, { key: 'ArrowRight' })
    expect(mutate).not.toHaveBeenCalled()
  })

  it('folds a column into a strip that keeps its tasks, and remembers it', () => {
    const { unmount } = renderBoard()
    fireEvent.click(screen.getByRole('button', { name: 'Collapse Done' }))
    const expand = screen.getByRole('button', { name: 'Expand Done' })
    expect(expand).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('link', { name: 'Rotate keys' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Task 4: Rotate keys (verified)' })).toHaveAttribute('href', '/sprints/7/tasks/4')

    unmount()
    renderBoard()
    expect(screen.getByRole('button', { name: 'Expand Done' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Collapse all' }))
    expect(screen.getAllByRole('button', { name: /^Expand / })).toHaveLength(6)
    expect(screen.getByRole('link', { name: 'Task 2: Tune the grader (needs a person)' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Expand all' }))
    expect(screen.getByRole('link', { name: 'Rotate keys' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Expand / })).not.toBeInTheDocument()
  })

  it('filters the cards by text and by preset, and says when a column has none', () => {
    renderBoard()
    expect(screen.getAllByText('No tasks')).toHaveLength(2)
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search tasks' }), { target: { value: 'docs' } })
    expect(screen.getByRole('link', { name: 'Write docs' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Rotate keys' })).not.toBeInTheDocument()
    expect(screen.getAllByText('No matching tasks')).toHaveLength(4)
    expect(screen.getByText('1 of 3 tasks shown')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Clear filters/ }))
    const needsPerson = screen.getByRole('button', { name: /^Needs a person/ })
    expect(needsPerson).toHaveTextContent('1')
    fireEvent.click(needsPerson)
    expect(needsPerson).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('link', { name: 'Tune the grader' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Write docs' })).not.toBeInTheDocument()
  })

  it('focuses the search with "/" and shows compact cards on demand', () => {
    renderBoard()
    fireEvent.keyDown(document.body, { key: '/' })
    expect(screen.getByRole('searchbox', { name: 'Search tasks' })).toHaveFocus()

    expect(within(cardOf('Write docs')).getByRole('combobox', { name: 'Change status' })).toBeInTheDocument()
    const compact = screen.getByRole('button', { name: 'Compact cards' })
    fireEvent.click(compact)
    expect(compact).toHaveAttribute('aria-pressed', 'true')
    expect(within(cardOf('Write docs')).queryByRole('combobox', { name: 'Change status' })).not.toBeInTheDocument()
    // The signals stay, with their group names for a screen reader.
    expect(within(cardOf('Tune the grader')).getByRole('group', { name: 'Observed' })).toBeInTheDocument()
  })

  it('lists the latest task messages as claims in the activity panel', () => {
    renderBoard()
    const toggle = screen.getByRole('button', { name: 'Activity' })
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    const panel = screen.getByRole('complementary', { name: 'Activity' })
    expect(within(panel).getByRole('heading', { name: 'Activity' })).toHaveFocus()
    expect(within(panel).getByText('NEEDS WORK: thresholds too loose')).toBeInTheDocument()
    expect(within(panel).getByText('Claim')).toBeInTheDocument()
    expect(within(panel).getByRole('link', { name: 'Tune the grader' })).toHaveAttribute('href', '/sprints/7/tasks/2')
    fireEvent.keyDown(panel, { key: 'Escape' })
    expect(screen.queryByRole('complementary', { name: 'Activity' })).not.toBeInTheDocument()
  })
})
