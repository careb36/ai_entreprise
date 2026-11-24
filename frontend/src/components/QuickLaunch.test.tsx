import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QuickLaunch } from './QuickLaunch'
import { useInbox, useInitCompany, usePlanSelfImprovementSprint, usePlanSprint, useSprints, useStartSprint } from '../hooks/useQueries'
import type { SprintPlanResponse } from '../services/api'
import { useSpecifications } from '../services/specifications'
import { useAppStore } from '../store/useAppStore'

vi.mock('../hooks/useQueries', () => ({
  useInitCompany: vi.fn(),
  usePlanSelfImprovementSprint: vi.fn(),
  usePlanSprint: vi.fn(),
  useStartSprint: vi.fn(),
  useInbox: vi.fn(),
  useSprints: vi.fn(),
}))
vi.mock('../services/specifications', () => ({ useSpecifications: vi.fn() }))

const plan: SprintPlanResponse = {
  sprint_name: 'Checkout sprint',
  goal: 'Build the checkout',
  scope_size: 'M',
  specification_id: 5,
  uncovered: ['REQ-2.1'],
  tasks: [
    { title: 'Cart listing', description: 'List the items', assignee: 'Developer', priority: 'high', covers: ['REQ-1.1', 'REQ-1.2'] },
    { title: 'Payment', description: 'Take the money', assignee: 'Developer', priority: 'high', covers: [], depends_on: [0] },
  ],
}

function mutation(overrides: Record<string, unknown> = {}) {
  return { isPending: false, error: null, reset: vi.fn(), mutateAsync: vi.fn(), ...overrides }
}

function renderPlanner() {
  return render(
    <MemoryRouter>
      <Routes>
        <Route path="/" element={<QuickLaunch onSuccess={vi.fn()} />} />
        <Route path="/sprints/23/board" element={<div data-testid="board" />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('planning a sprint from a specification', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ companyId: 42, companyName: 'Example', activeSprintId: null })
    vi.mocked(useInitCompany).mockReturnValue(mutation() as never)
    vi.mocked(usePlanSprint).mockReturnValue(mutation({ mutateAsync: vi.fn().mockResolvedValue(plan) }) as never)
    vi.mocked(usePlanSelfImprovementSprint).mockReturnValue(mutation() as never)
    vi.mocked(useStartSprint).mockReturnValue(mutation({ mutateAsync: vi.fn().mockResolvedValue({ sprint_id: 23 }) }) as never)
    vi.mocked(useInbox).mockReturnValue({ data: { blocked_tasks: [], proposals: [], pending_runs: 0, total: 0 } } as never)
    vi.mocked(useSprints).mockReturnValue({ data: [] } as never)
    vi.mocked(useSpecifications).mockReturnValue({ data: { specifications: [
      { id: 5, title: 'Checkout', status: 'approved', version: 2 },
      { id: 6, title: 'Returns', status: 'draft', version: 1 },
      { id: 4, title: 'Old checkout', status: 'superseded', version: 1 },
    ] } } as never)
  })

  it('offers only the approved specifications, and nothing when there is none', () => {
    const { unmount } = renderPlanner()
    const select = screen.getByRole('combobox', { name: 'Plan from a specification' })
    expect(within(select).getAllByRole('option').map(option => option.textContent)).toEqual(['None: plan from the goal', 'Checkout (v2)'])
    expect(screen.getByRole('button', { name: 'Generate Plan' })).toBeDisabled()
    unmount()

    vi.mocked(useSpecifications).mockReturnValue({ data: { specifications: [] } } as never)
    renderPlanner()
    expect(screen.queryByRole('combobox', { name: 'Plan from a specification' })).not.toBeInTheDocument()
  })

  it('plans without a goal, shows what each task covers and what stays uncovered, and launches with the specification', async () => {
    const planMutation = mutation({ mutateAsync: vi.fn().mockResolvedValue(plan) })
    const startMutation = mutation({ mutateAsync: vi.fn().mockResolvedValue({ sprint_id: 23 }) })
    vi.mocked(usePlanSprint).mockReturnValue(planMutation as never)
    vi.mocked(useStartSprint).mockReturnValue(startMutation as never)
    renderPlanner()

    fireEvent.change(screen.getByRole('combobox', { name: 'Plan from a specification' }), { target: { value: '5' } })
    expect(screen.getByPlaceholderText('The specification is the goal; add a note for the PO if you want.')).toBeInTheDocument()
    const generate = screen.getByRole('button', { name: 'Generate Plan' })
    expect(generate).toBeEnabled()
    fireEvent.click(generate)

    expect(await screen.findByText('Plan Preview')).toBeInTheDocument()
    expect(planMutation.mutateAsync).toHaveBeenCalledWith({ company_id: 42, scope_size: 'M', specification_id: 5 })
    expect(screen.getByText('REQ-1.1')).toBeInTheDocument()
    expect(screen.getByText('REQ-1.2')).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent('1 criterion stays uncovered: REQ-2.1. The sprint can still start.')

    fireEvent.click(screen.getByRole('button', { name: 'Launch Sprint' }))
    expect(await screen.findByTestId('board')).toBeInTheDocument()
    expect(startMutation.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({
      company_id: 42,
      specification_id: 5,
      backlog_items: plan.tasks,
    }))
    expect(startMutation.mutateAsync.mock.calls[0][0].backlog_items[0].covers).toEqual(['REQ-1.1', 'REQ-1.2'])
  })

  it('shows which tasks a planned task comes after, and launches with the dependencies', async () => {
    const startMutation = mutation({ mutateAsync: vi.fn().mockResolvedValue({ sprint_id: 23 }) })
    vi.mocked(useStartSprint).mockReturnValue(startMutation as never)
    renderPlanner()

    fireEvent.change(screen.getByPlaceholderText('Describe the sprint goal…'), { target: { value: 'Take payments' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Plan' }))
    expect(await screen.findByText('Plan Preview')).toBeInTheDocument()
    expect(screen.getByText('After: Cart listing')).toBeInTheDocument()
    expect(screen.getAllByText(/^After:/)).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: 'Launch Sprint' }))
    expect(await screen.findByTestId('board')).toBeInTheDocument()
    const items = startMutation.mutateAsync.mock.calls[0][0].backlog_items
    expect(items[0].depends_on).toBeUndefined()
    expect(items[1].depends_on).toEqual([0])
  })

  it('sends the goal beside the specification when both are given, and neither note nor chips without one', async () => {
    const planMutation = mutation({ mutateAsync: vi.fn().mockResolvedValue({ ...plan, specification_id: null, uncovered: [], tasks: [{ ...plan.tasks[1], depends_on: [] }] }) })
    vi.mocked(usePlanSprint).mockReturnValue(planMutation as never)
    renderPlanner()

    fireEvent.change(screen.getByPlaceholderText('Describe the sprint goal…'), { target: { value: 'Take payments' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Plan' }))
    expect(await screen.findByText('Plan Preview')).toBeInTheDocument()
    expect(planMutation.mutateAsync).toHaveBeenCalledWith({ company_id: 42, prompt: 'Take payments', scope_size: 'M' })
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    expect(screen.queryByText('Covers')).not.toBeInTheDocument()
  })
})

describe('a plan made without the planner model', () => {
  const objective = `# Sprint\n\n${'## 1. Item one\n- WHEN a person writes, THE SYSTEM SHALL keep it.\n'.repeat(80)}`
  const fallbackPlan: SprintPlanResponse = {
    sprint_name: 'Sprint - Sprint…',
    goal: objective,
    scope_size: 'S',
    objective,
    plan_id: 'abc123',
    planner_fallback: { reason: 'model_error', detail: 'timeout after 30s' },
    unplanned_items: ['4. Item four'],
    tasks: [{ title: 'Refine', description: `Objective:\n${objective}`, assignee: 'PO', priority: 'critical' }],
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ companyId: 42, companyName: 'Example', activeSprintId: null })
    vi.mocked(useInitCompany).mockReturnValue(mutation() as never)
    vi.mocked(usePlanSelfImprovementSprint).mockReturnValue(mutation() as never)
    vi.mocked(useInbox).mockReturnValue({ data: { blocked_tasks: [], proposals: [], pending_runs: 0, total: 0 } } as never)
    vi.mocked(useSprints).mockReturnValue({ data: [] } as never)
    vi.mocked(useSpecifications).mockReturnValue({ data: { specifications: [] } } as never)
  })

  it('says so with the observed reason, offers a retry, and launches with the objective whole and the plan id', async () => {
    const planMutation = mutation({ mutateAsync: vi.fn().mockResolvedValue(fallbackPlan) })
    const startMutation = mutation({ mutateAsync: vi.fn().mockResolvedValue({ sprint_id: 23 }) })
    vi.mocked(usePlanSprint).mockReturnValue(planMutation as never)
    vi.mocked(useStartSprint).mockReturnValue(startMutation as never)
    renderPlanner()

    fireEvent.change(screen.getByPlaceholderText('Describe the sprint goal…'), { target: { value: objective } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Plan' }))
    const note = await screen.findByRole('alert')
    expect(note).toHaveTextContent("The planner's model did not answer; this is a generic plan.")
    expect(note).toHaveTextContent('Observed: timeout after 30s')
    expect(note).toHaveTextContent('1 item did not fit the scope and is not a task: 4. Item four.')

    fireEvent.click(within(note).getByRole('button', { name: 'Retry planning' }))
    await waitFor(() => expect(planMutation.mutateAsync).toHaveBeenCalledTimes(2))
    expect(await screen.findByRole('alert')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Launch Sprint' }))
    expect(await screen.findByTestId('board')).toBeInTheDocument()
    expect(startMutation.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ objective, plan_id: 'abc123', goal: objective }))
  })

  it('shows what the PO says is already done on the preview, and launches with the plan id', async () => {
    const startMutation = mutation({ mutateAsync: vi.fn().mockResolvedValue({ sprint_id: 23 }) })
    vi.mocked(usePlanSprint).mockReturnValue(mutation({ mutateAsync: vi.fn().mockResolvedValue({
      sprint_name: 'Sprint - greet', goal: 'Verify', scope_size: 'S', planner_fallback: null, plan_id: 'def456',
      tasks: [{ title: 'Verify that the objective is already met', description: 'Run the tests', assignee: 'QA', priority: 'high', depends_on: [] }],
      already_done_claim: [{ part: 'the option', evidence: '', paths: ['src/cli.py'] }],
      already_done_observed: { files_read: [], searches: [], claimed_paths: [{ path: 'src/cli.py', read: false, files: [] }] },
    }) }) as never)
    vi.mocked(useStartSprint).mockReturnValue(startMutation as never)
    renderPlanner()

    fireEvent.change(screen.getByPlaceholderText('Describe the sprint goal…'), { target: { value: 'Add the option' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Plan' }))
    const note = await screen.findByRole('region', { name: 'The PO says part of this objective is already done' })
    expect(within(note).getByText('not read').closest('code')).toHaveTextContent('src/cli.py')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Launch Sprint' }))
    expect(await screen.findByTestId('board')).toBeInTheDocument()
    expect(startMutation.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ plan_id: 'def456' }))
  })

  it('starts from the objective a board hands back for planning again', () => {
    vi.mocked(usePlanSprint).mockReturnValue(mutation() as never)
    vi.mocked(useStartSprint).mockReturnValue(mutation() as never)
    render(
      <MemoryRouter initialEntries={[{ pathname: '/', state: { objective } }]}>
        <QuickLaunch onSuccess={vi.fn()} />
      </MemoryRouter>,
    )
    expect(screen.getByPlaceholderText('Describe the sprint goal…')).toHaveValue(objective)
  })
})
