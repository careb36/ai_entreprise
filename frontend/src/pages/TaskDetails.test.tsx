import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TaskDetails from './TaskDetails'
import { useAgentRuns, useCapabilities, useSprintBoard } from '../hooks/useQueries'
import { ApiError } from '../services/api'
import { useTaskCost } from '../services/cost'
import { useAppStore } from '../store/useAppStore'

vi.mock('../services/cost', async importOriginal => ({
  ...(await importOriginal<typeof import('../services/cost')>()),
  useTaskCost: vi.fn(() => ({ data: undefined })),
}))
// The relations section reads its own data (components/TaskRelations.test.tsx).
vi.mock('../components/TaskRelations', () => ({ TaskRelations: () => null }))
// So do the run secrets (components/RunSecretsPanel.test.tsx).
vi.mock('../components/RunSecretsPanel', () => ({ RunSecretsPanel: () => null }))
// So does the event log (components/TaskEventLog.test.tsx).
vi.mock('../components/TaskEventLog', () => ({ TaskEventLog: () => null }))
vi.mock('../hooks/useQueries', () => ({
  useSprintBoard: vi.fn(),
  useAgentRuns: vi.fn(),
  useCapabilities: vi.fn(),
  useAgentMessages: vi.fn(() => ({ data: { messages: [] }, isLoading: false, isError: false })),
}))
const boardQuery = vi.mocked(useSprintBoard)
const runsQuery = vi.mocked(useAgentRuns)
const capabilitiesQuery = vi.mocked(useCapabilities)

function setup(path = '/task/12') {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/task/:taskId" element={<TaskDetails />} />
    <Route path="/sprints/:sprintId/tasks/:taskId" element={<TaskDetails />} />
  </Routes></MemoryRouter>)
}

describe('task detail errors', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ activeSprintId: 7 })
    capabilitiesQuery.mockReturnValue({ data: undefined } as never)
    boardQuery.mockReturnValue({ data: { columns: {} }, isLoading: false, isError: false } as never)
    runsQuery.mockReturnValue({ data: { agent_runs: [] }, isLoading: false, isError: false } as never)
  })

  it.each(['bad', '12x', '0', '-1', '1.5'])('rejects malformed task ID %s without loading a board', id => {
    setup(`/task/${id}`)
    expect(screen.getByRole('alert')).toHaveTextContent(`Invalid task ID: ${id}`)
    expect(boardQuery).toHaveBeenCalledWith(null)
  })

  it('names the task and the searched sprint when the loaded board has no task', () => {
    setup()
    expect(screen.getByRole('alert')).toHaveTextContent('Task #12 was not found in sprint #7')
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })

  it('rejects a malformed explicit sprint ID without loading a board', () => {
    setup('/sprints/7x/tasks/12')
    expect(screen.getByRole('alert')).toHaveTextContent('Invalid sprint ID: 7x')
    expect(boardQuery).toHaveBeenCalledWith(null)
  })

  it('still names the searched sprint after the missing selection is cleared', () => {
    boardQuery.mockReturnValue({ isError: true, error: new ApiError(404, 'missing') } as never)
    const view = setup()
    useAppStore.setState({ activeSprintId: null })
    view.rerender(<MemoryRouter initialEntries={['/task/12']}><Routes>
      <Route path="/task/:taskId" element={<TaskDetails />} />
    </Routes></MemoryRouter>)
    expect(screen.getByRole('alert')).toHaveTextContent('Sprint #7 was not found while looking for task #12')
    expect(screen.getByRole('link')).toHaveAttribute('href', '/sprints')
  })

  it('distinguishes a missing sprint from a missing task', () => {
    boardQuery.mockReturnValue({ isError: true, error: new ApiError(404, 'missing') } as never)
    setup('/sprints/9/tasks/12')
    expect(screen.getByRole('alert')).toHaveTextContent('Sprint #9 was not found while looking for task #12')
    expect(boardQuery).toHaveBeenCalledWith(9)
  })

  it('names the task and sprint on load failure and retries the board', () => {
    const refetch = vi.fn()
    boardQuery.mockReturnValue({ isError: true, error: new ApiError(503, 'failed'), refetch } as never)
    setup()
    expect(screen.getByRole('alert')).toHaveTextContent('Failed to load sprint #7 for task #12')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(refetch).toHaveBeenCalledOnce()
  })

  it('offers the sprint list when there is no selected sprint', () => {
    useAppStore.setState({ activeSprintId: null })
    setup()
    expect(screen.getByRole('alert')).toHaveTextContent('Select a sprint to look up task #12')
    expect(screen.getByRole('link')).toHaveAttribute('href', '/sprints')
  })

  it.each(['/sprints/9/tasks/12', '/task/12'])('shows the Sprints › sprint › task breadcrumb at %s', path => {
    useAppStore.setState({ activeSprintId: 9 })
    boardQuery.mockReturnValue({
      data: {
        sprint: { id: 9, sprint_name: 'Hardening' },
        columns: { done: [{
          id: 12, sprint_id: 9, title: 'Encrypt credentials', description: 'Deliverable: crypto.py',
          status: 'done', priority: 'high', assignee: 'Developer', comments: [],
          created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z',
        }] },
      },
      isLoading: false,
      isError: false,
    } as never)
    setup(path)
    const breadcrumb = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(breadcrumb).getAllByRole('listitem').map(item => item.textContent))
      .toEqual(['Sprints', 'Hardening', 'Encrypt credentials'])
    expect(within(breadcrumb).getByRole('link', { name: 'Sprints' })).toHaveAttribute('href', '/sprints')
    expect(within(breadcrumb).getByRole('link', { name: 'Hardening' })).toHaveAttribute('href', '/sprints/9/board')
    expect(within(breadcrumb).queryByRole('link', { name: 'Encrypt credentials' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Encrypt credentials' })).toBeInTheDocument()
    expect(screen.getByText('Deliverable: crypto.py')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Back' })).not.toBeInTheDocument()
    expect(boardQuery).toHaveBeenCalledWith(9)
  })
})

describe('carry-over lineage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ companyId: 42, activeSprintId: 9 })
    capabilitiesQuery.mockReturnValue({ data: { memory: false } } as never)
  })

  it('links the earlier attempt and the later one', () => {
    boardQuery.mockReturnValue({
      data: {
        sprint: { id: 9, sprint_name: 'Hardening' },
        columns: { blocked: [{
          id: 12, sprint_id: 9, title: 'Encrypt credentials', description: '',
          status: 'blocked', priority: 'high', assignee: 'Developer',
          comments: [
            { author: 'System', text: 'Carried over from task 7 ("Sprint 0"), which was blocked: too short', created_at: '' },
            { author: 'System', text: 'Carried over to sprint 10 ("Sprint 2") as task 15 by ana.', created_at: '' },
          ],
          created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z',
        }] },
      },
      isLoading: false,
      isError: false,
    } as never)
    setup('/sprints/9/tasks/12')
    const lineage = screen.getByRole('navigation', { name: 'Earlier and later attempts' })
    expect(lineage).toHaveTextContent('Another attempt at a task blocked in Sprint 0: task #7')
    expect(within(lineage).getByRole('link', { name: 'task #7' })).toHaveAttribute('href', '/task/7')
    expect(lineage).toHaveTextContent('Given another attempt in Sprint 2: task #15')
    expect(within(lineage).getByRole('link', { name: 'task #15' })).toHaveAttribute('href', '/sprints/10/tasks/15')
  })

  it("shows the task's cost, or that its price is unknown", () => {
    boardQuery.mockReturnValue({
      data: {
        sprint: { id: 9, sprint_name: 'Hardening' },
        columns: { done: [{
          id: 12, sprint_id: 9, title: 'Encrypt credentials', description: '',
          status: 'done', priority: 'high', assignee: 'Developer', comments: [],
          created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z',
        }] },
      },
      isLoading: false,
      isError: false,
    } as never)
    runsQuery.mockReturnValue({ data: { agent_runs: [] }, isLoading: false, isError: false } as never)
    vi.mocked(useTaskCost).mockReturnValue({
      data: {
        task_id: 12, runs: [], turns: 1, reported_turns: 1, estimated_turns: 0, unavailable_turns: 0,
        reported_tokens: 20, input_tokens: 10, output_tokens: 10, estimated_tokens: 0,
        cost_cents: null, reason: 'price_unknown', unpriced_models: ['mystery'], models: ['mystery'], price_source: 'models.dev',
      },
    } as never)
    setup('/sprints/9/tasks/12')
    const card = screen.getByRole('region', { name: 'Cost of this task' })
    expect(card).toHaveTextContent('price unknown')
    expect(card).not.toHaveTextContent('$')
    expect(useTaskCost).toHaveBeenCalledWith(12)
  })

  it('shows nothing for an ordinary task', () => {
    boardQuery.mockReturnValue({
      data: {
        sprint: { id: 9, sprint_name: 'Hardening' },
        columns: { done: [{
          id: 12, sprint_id: 9, title: 'Encrypt credentials', description: '',
          status: 'done', priority: 'high', assignee: 'Developer', comments: [{ author: 'Dev', text: 'Done.', created_at: '' }],
          created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z',
        }] },
      },
      isLoading: false,
      isError: false,
    } as never)
    setup('/sprints/9/tasks/12')
    expect(screen.queryByRole('navigation', { name: 'Earlier and later attempts' })).not.toBeInTheDocument()
  })
})

describe('the criteria a task covers', () => {
  function board(sprint: Record<string, unknown>, covers?: string[]) {
    return {
      data: {
        sprint: { id: 9, sprint_name: 'Hardening', ...sprint },
        columns: { done: [{
          id: 12, sprint_id: 9, title: 'Cart listing', description: '', status: 'done', priority: 'high', assignee: 'Developer',
          comments: [], covers, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z',
        }] },
      },
      isLoading: false,
      isError: false,
    }
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ companyId: 42, activeSprintId: 9 })
    capabilitiesQuery.mockReturnValue({ data: { memory: false } } as never)
    runsQuery.mockReturnValue({ data: { agent_runs: [] }, isLoading: false, isError: false } as never)
  })

  it('links each covered criterion to the specification the sprint was planned from', () => {
    boardQuery.mockReturnValue(board({ specification_id: 5 }, ['REQ-1.1', 'REQ-1.2']) as never)
    setup('/sprints/9/tasks/12')
    const covers = screen.getByLabelText('Covers')
    expect(within(covers).getAllByRole('link').map(link => [link.textContent, link.getAttribute('href')])).toEqual([
      ['REQ-1.1', '/company/specifications/5'],
      ['REQ-1.2', '/company/specifications/5'],
    ])
  })

  it.each([
    ['no specification', { specification_id: null }, ['REQ-1.1']],
    ['no criteria', { specification_id: 5 }, []],
    ['an older board without the field', {}, undefined],
  ])('shows nothing with %s', (_, sprint, covers) => {
    boardQuery.mockReturnValue(board(sprint, covers) as never)
    setup('/sprints/9/tasks/12')
    expect(screen.queryByLabelText('Covers')).not.toBeInTheDocument()
  })
})

describe('the tasks a task waits for', () => {
  function board(task: Record<string, unknown>) {
    const base = { sprint_id: 9, description: '', priority: 'high', assignee: 'Developer', comments: [], created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z' }
    return {
      data: {
        sprint: { id: 9, sprint_name: 'Hardening' },
        columns: {
          done: [{ ...base, id: 10, title: 'Store', status: 'done' }],
          todo: [{ ...base, id: 12, title: 'Checkout', status: 'todo', ...task }],
        },
      },
      isLoading: false,
      isError: false,
    }
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ companyId: 42, activeSprintId: 9 })
    capabilitiesQuery.mockReturnValue({ data: { memory: false } } as never)
    runsQuery.mockReturnValue({ data: { agent_runs: [] }, isLoading: false, isError: false } as never)
  })

  it('links each task still waited for, with its title when the board has it', () => {
    boardQuery.mockReturnValue(board({ depends_on: [10, 11], waiting_for: [10, 11] }) as never)
    setup('/sprints/9/tasks/12')
    const waits = screen.getByLabelText('Waits for')
    expect(within(waits).getAllByRole('link').map(link => [link.textContent, link.getAttribute('href')])).toEqual([
      ['task #10: Store', '/sprints/9/tasks/10'],
      ['task #11', '/sprints/9/tasks/11'],
    ])
    expect(screen.queryByLabelText('Depended on')).not.toBeInTheDocument()
  })

  it('shows the dependencies more quietly once none is waited for', () => {
    boardQuery.mockReturnValue(board({ depends_on: [10], waiting_for: [] }) as never)
    setup('/sprints/9/tasks/12')
    const depended = screen.getByLabelText('Depended on')
    expect(within(depended).getByRole('link', { name: 'task #10: Store' })).toHaveAttribute('href', '/sprints/9/tasks/10')
    expect(screen.queryByLabelText('Waits for')).not.toBeInTheDocument()
  })

  it.each([
    ['no dependencies', { depends_on: [], waiting_for: [] }],
    ['an older board without the fields', {}],
  ])('shows nothing with %s', (_, task) => {
    boardQuery.mockReturnValue(board(task) as never)
    setup('/sprints/9/tasks/12')
    expect(screen.queryByLabelText('Waits for')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Depended on')).not.toBeInTheDocument()
  })
})

describe('task delivery evidence', () => {
  const boardWithTask = {
    data: {
      sprint: { id: 9, sprint_name: 'Hardening' },
      columns: { in_review: [{
        id: 12, sprint_id: 9, title: 'Encrypt credentials', description: '',
        status: 'in_review', priority: 'high', assignee: 'Developer', comments: [],
        created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z',
      }] },
    },
    isLoading: false,
    isError: false,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ companyId: 42, activeSprintId: 9 })
    boardQuery.mockReturnValue(boardWithTask as never)
    capabilitiesQuery.mockReturnValue({ data: { memory: false } } as never)
  })

  it('links each claimed file to its lineage when the knowledge graph is connected', () => {
    capabilitiesQuery.mockReturnValue({ data: { memory: true, memory_graph: true } } as never)
    runsQuery.mockReturnValue({
      data: { agent_runs: [{ id: 31, version: 1, status: 'verified', claims: { files: ['src/a b.py'] } }] },
      isLoading: false,
      isError: false,
    } as never)
    setup('/sprints/9/tasks/12')

    expect(within(evidenceSection()).getByRole('link', { name: 'src/a b.py' }))
      .toHaveAttribute('href', '/lineage?file=src%2Fa+b.py')
  })

  function evidenceSection() {
    return screen.getByRole('region', { name: 'Evidence' })
  }

  it("shows the latest run's verdict, version, claimed files and a link to its evidence", () => {
    runsQuery.mockReturnValue({
      data: { agent_runs: [{
        id: 31, company_id: 42, external_ref: 'task-12', version: 3, producer: 'le-fabrique-task-worker',
        model: null, agent_role: 'Developer', subject: { sprint_id: 9, backlog_item_id: 12 },
        claims: { files: ['backend/app/crypto.py', 'backend/tests/test_crypto.py'], summary: 'done' },
        artifact_digest: 'd', artifact_size: 10, status: 'verified', started_at: null, finished_at: null,
        ingested_at: '2026-10-02T00:00:00Z', audit_log_id: null, latest_approval: null,
      }] },
      isLoading: false,
      isError: false,
    } as never)
    setup('/sprints/9/tasks/12')

    expect(runsQuery).toHaveBeenCalledWith(42, {
      producer: 'le-fabrique-task-worker',
      external_ref: 'task-12',
      limit: 1,
    })
    const section = evidenceSection()
    expect(within(section).getByTestId('verdict-verified')).toHaveTextContent('Verified')
    expect(within(section).getByText('Version 3')).toBeInTheDocument()
    expect(within(section).getByText('backend/app/crypto.py')).toBeInTheDocument()
    expect(within(section).queryByRole('link', { name: 'backend/app/crypto.py' })).not.toBeInTheDocument()
    expect(within(section).getByText('backend/tests/test_crypto.py')).toBeInTheDocument()
    expect(within(section).getByRole('link', { name: 'View evidence' })).toHaveAttribute('href', '/evidence/31')
    expect(within(section).queryByText('No evidence recorded yet.')).not.toBeInTheDocument()
  })

  it('says so when no run has been recorded for the task', () => {
    runsQuery.mockReturnValue({ data: { agent_runs: [] }, isLoading: false, isError: false } as never)
    setup('/sprints/9/tasks/12')

    const section = evidenceSection()
    expect(within(section).getByText('No evidence recorded yet.')).toBeInTheDocument()
    expect(within(section).queryByRole('link', { name: 'View evidence' })).not.toBeInTheDocument()
    expect(within(section).queryByTestId(/^verdict-/)).not.toBeInTheDocument()
  })

  it('reports a failed evidence lookup and retries it', () => {
    const refetch = vi.fn()
    runsQuery.mockReturnValue({ isLoading: false, isError: true, refetch } as never)
    setup('/sprints/9/tasks/12')

    const section = evidenceSection()
    expect(within(section).getByRole('alert')).toHaveTextContent('Failed to load the evidence for this task.')
    fireEvent.click(within(section).getByRole('button', { name: 'Retry' }))
    expect(refetch).toHaveBeenCalledOnce()
  })
})
