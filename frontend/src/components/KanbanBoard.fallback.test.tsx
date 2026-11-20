import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import KanbanBoard from './KanbanBoard'
import { useCurrentRole, useSprintBoard, useUpdateBacklogItem } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'

vi.mock('../hooks/useQueries', async importOriginal => ({
  ...(await importOriginal<typeof import('../hooks/useQueries')>()),
  useCurrentRole: vi.fn(),
  useSprintBoard: vi.fn(),
  useUpdateBacklogItem: vi.fn(),
}))

const objective = `# Sprint\n\n${'## Item\n- WHEN a person writes, THE SYSTEM SHALL keep it whole.\n'.repeat(60)}`

function HandedObjective() {
  const state = useLocation().state as { objective?: string } | null
  return <p data-testid="planner">{state?.objective}</p>
}

function renderBoard(sprint: Record<string, unknown>) {
  vi.mocked(useSprintBoard).mockReturnValue({
    data: { sprint: { id: 7, sprint_name: 'Sprint - Sprint…', ...sprint }, columns: { todo: [] }, total: 0 },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/sprints/7/board']}>
        <Routes>
          <Route path="/sprints/7/board" element={<KanbanBoard sprintId="7" />} />
          <Route path="/sprints" element={<HandedObjective />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('a board planned without the planner model', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ companyId: 42 })
    vi.mocked(useUpdateBacklogItem).mockReturnValue({ mutate: vi.fn(), isPending: false } as never)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({ agent_runs: [] }) }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('says so, shows the objective whole, and a Manager plans again from it', () => {
    vi.mocked(useCurrentRole).mockReturnValue('Manager' as never)
    renderBoard({ objective, planner_fallback: { reason: 'no_model', detail: 'No planning model is assigned to the PO.' } })
    expect(screen.getByRole('alert')).toHaveTextContent("The planner's model did not answer; this is a generic plan.")
    expect(screen.getByText(`The objective as written (${objective.length} characters)`)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry planning' }))
    expect(screen.getByTestId('planner').textContent).toBe(objective)
  })

  it('offers a Viewer no retry, and shows nothing when the model planned', () => {
    vi.mocked(useCurrentRole).mockReturnValue('Viewer' as never)
    const { unmount } = renderBoard({ objective, planner_fallback: { reason: 'model_error', detail: 'HTTP 503' } })
    expect(screen.getByRole('alert')).toHaveTextContent('Observed: HTTP 503')
    expect(screen.queryByRole('button', { name: 'Retry planning' })).not.toBeInTheDocument()
    unmount()

    renderBoard({ objective: null, planner_fallback: null })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
