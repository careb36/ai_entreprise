import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import KanbanBoard from './KanbanBoard'
import { useCurrentRole, useSprintBoard, useUpdateBacklogItem } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'

// What a person's move on the board does: a card moved to To do says whether
// it was queued, whether its sprint reopened, or why it waits; a Manager moves
// a blocked or To do card to another sprint, a Viewer sees no such action.
vi.mock('../hooks/useQueries', async importOriginal => ({
  ...(await importOriginal<typeof import('../hooks/useQueries')>()),
  useSprintBoard: vi.fn(),
  useUpdateBacklogItem: vi.fn(),
  useCurrentRole: vi.fn(),
}))

function makeItem(id: number, title: string, status: string) {
  return {
    id, sprint_id: 7, title, description: '', status, priority: 'high', assignee: 'Developer',
    comments: [], created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  }
}

const blocked = makeItem(2, 'Encrypt credentials', 'blocked')
const done = makeItem(3, 'Rotate keys', 'done')

type Handler = (url: URL, init?: RequestInit) => { status?: number; body: unknown }

function stubFetch(handler: Handler) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost')
    const { status = 200, body } = url.pathname === '/v1/agent_runs'
      ? { body: { agent_runs: [] } }
      : handler(url, init)
    return Promise.resolve({
      ok: status < 400, status, headers: new Headers(), json: () => Promise.resolve(body),
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderBoard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><KanbanBoard sprintId="7" /></MemoryRouter>
    </QueryClientProvider>,
  )
}

/** The board's notice; dnd-kit keeps a live region of its own with the same role. */
function notice() {
  return screen.queryAllByRole('status').find(element => !element.id.startsWith('DndLiveRegion')) ?? null
}

function cardOf(title: string) {
  return screen.getByRole('link', { name: title }).closest('.card') as HTMLElement
}

describe('Moves on the board', () => {
  const mutate = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ companyId: 42 })
    vi.mocked(useSprintBoard).mockReturnValue({
      data: { sprint: { id: 7, sprint_name: 'Hardening' }, columns: { blocked: [blocked], done: [done] }, total: 2 },
      isLoading: false, isError: false, refetch: vi.fn(),
    } as never)
    vi.mocked(useUpdateBacklogItem).mockReturnValue({ mutate, isPending: false } as never)
    vi.mocked(useCurrentRole).mockReturnValue('Manager')
    stubFetch(() => ({ body: {} }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function moveToTodo(answer: Record<string, unknown>) {
    mutate.mockImplementation((_vars, options) => options?.onSuccess?.({ ...blocked, status: 'todo', ...answer }))
    renderBoard()
    fireEvent.change(within(cardOf('Encrypt credentials')).getByRole('combobox', { name: 'Change status' }),
      { target: { value: 'todo' } })
    expect(mutate).toHaveBeenCalledWith({ itemId: 2, data: { status: 'todo' } }, expect.anything())
  }

  it('says the sprint was reopened and the task queued', () => {
    moveToTodo({ queued: true, reopened: true, sprint_status: 'active', reason: null })
    expect(notice()).toHaveTextContent('The sprint was reopened and the task was queued for its agent.')
  })

  it('says a paused sprint keeps the task waiting', () => {
    moveToTodo({ queued: false, reopened: false, sprint_status: 'paused', reason: 'the sprint is paused: resume it to run its tasks' })
    expect(notice()).toHaveTextContent('The sprint is paused: resume it to run this task.')
  })

  it('says nothing for a change that is not a move to To do', () => {
    moveToTodo({})
    expect(notice()).toBeNull()
  })

  it('shows the refusal of an archived sprint', () => {
    mutate.mockImplementation((_vars, options) => options?.onError?.(new Error('The sprint is archived')))
    renderBoard()
    fireEvent.change(within(cardOf('Encrypt credentials')).getByRole('combobox', { name: 'Change status' }),
      { target: { value: 'todo' } })
    expect(screen.getByRole('alert')).toHaveTextContent('The sprint is archived')
  })

  it('moves a blocked card to another open sprint', async () => {
    const fetchMock = stubFetch((url, init) => {
      if (url.pathname === '/v1/backlog/2/carry_targets') {
        return { body: { sprints: [{ id: 9, sprint_name: 'Sprint 1', status: 'active' }] } }
      }
      if (url.pathname === '/v1/backlog/2/carry_over' && init?.method === 'POST') {
        return { body: { original_id: 2, backlog_item_id: 20, sprint_id: 9, sprint_name: 'Sprint 1',
          sprint_status: 'active', created_sprint: false, queued: true, reason: null } }
      }
      return { status: 404, body: { detail: 'unexpected' } }
    })
    renderBoard()

    // Only the blocked card offers it.
    expect(within(cardOf('Rotate keys')).queryByRole('button', { name: 'Move to sprint…' })).not.toBeInTheDocument()
    fireEvent.click(within(cardOf('Encrypt credentials')).getByRole('button', { name: 'Move to sprint…' }))
    const picker = within(cardOf('Encrypt credentials')).getByRole('combobox', { name: 'Sprint to move the task to' })
    await screen.findByRole('option', { name: 'Sprint 1 (active)' })
    expect(within(picker).getByRole('option', { name: 'A new sprint' })).toBeInTheDocument()
    fireEvent.change(picker, { target: { value: '9' } })
    fireEvent.click(within(cardOf('Encrypt credentials')).getByRole('button', { name: 'Move' }))

    await waitFor(() => expect(notice()).toHaveTextContent('Task moved to "Sprint 1".'))
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ target_sprint_id: 9 })
  })

  it('moves a card to a new sprint and shows a refusal', async () => {
    stubFetch(url => (url.pathname.endsWith('/carry_targets')
      ? { body: { sprints: [] } }
      : { status: 409, body: { detail: 'Task 2 was already carried over' } }))
    renderBoard()

    fireEvent.click(within(cardOf('Encrypt credentials')).getByRole('button', { name: 'Move to sprint…' }))
    const picker = within(cardOf('Encrypt credentials')).getByRole('combobox', { name: 'Sprint to move the task to' })
    fireEvent.change(picker, { target: { value: 'new' } })
    fireEvent.click(within(cardOf('Encrypt credentials')).getByRole('button', { name: 'Move' }))

    await waitFor(() => expect(within(cardOf('Encrypt credentials')).getByRole('alert'))
      .toHaveTextContent('Task 2 was already carried over'))
  })

  it('offers a Viewer no move to another sprint', () => {
    vi.mocked(useCurrentRole).mockReturnValue('Viewer')
    renderBoard()
    expect(screen.queryByRole('button', { name: 'Move to sprint…' })).not.toBeInTheDocument()
  })
})
