import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QuickLaunch } from './QuickLaunch'
import { SprintTeamChips } from './SprintTeam'
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

const docsPlan: SprintPlanResponse = {
  sprint_name: 'Docs sprint',
  goal: 'Document the project',
  scope_size: 'M',
  tasks: [{ title: 'Write the README', description: 'All of it', assignee: 'TechWriter', priority: 'high' }],
  hired_roles: ['BackendDeveloper', 'PO', 'QA', 'TechWriter'],
  suggested_team: ['TechWriter'],
  team_reasons: { TechWriter: 'the request is documentation only' },
}

const backendPlan: SprintPlanResponse = {
  ...docsPlan,
  tasks: [
    { title: 'Add the endpoint', description: 'With tests', assignee: 'BackendDeveloper', priority: 'high' },
    { title: 'Document it', description: 'API docs', assignee: 'TechWriter', priority: 'medium' },
  ],
  suggested_team: ['BackendDeveloper', 'TechWriter'],
  team_reasons: {},
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

async function plan() {
  fireEvent.change(screen.getByPlaceholderText('Describe the sprint goal…'), { target: { value: 'Document the project' } })
  fireEvent.click(screen.getByRole('button', { name: 'Generate Plan' }))
  expect(await screen.findByText('Plan Preview')).toBeInTheDocument()
}

describe("the sprint's team in the planner", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ companyId: 42, companyName: 'Example', activeSprintId: null })
    vi.mocked(useInitCompany).mockReturnValue(mutation() as never)
    vi.mocked(usePlanSelfImprovementSprint).mockReturnValue(mutation() as never)
    vi.mocked(useInbox).mockReturnValue({ data: { blocked_tasks: [], proposals: [], pending_runs: 0, total: 0 } } as never)
    vi.mocked(useSprints).mockReturnValue({ data: [] } as never)
    vi.mocked(useSpecifications).mockReturnValue({ data: { specifications: [] } } as never)
  })

  it("ticks the PO's suggestion over the hired roles, with its reason, and launches with that team", async () => {
    const start = mutation({ mutateAsync: vi.fn().mockResolvedValue({ sprint_id: 23 }) })
    vi.mocked(usePlanSprint).mockReturnValue(mutation({ mutateAsync: vi.fn().mockResolvedValue(docsPlan) }) as never)
    vi.mocked(useStartSprint).mockReturnValue(start as never)
    renderPlanner()
    await plan()

    expect(screen.getByRole('group', { name: 'Sprint team' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /Technical Writer/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /Backend Developer/ })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: /QA Engineer/ })).not.toBeChecked()
    expect(screen.getByText('PO: the request is documentation only')).toBeInTheDocument()
    // The only role left on the team cannot be unticked.
    expect(screen.getByRole('checkbox', { name: /Technical Writer/ })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Launch Sprint' }))
    expect(await screen.findByTestId('board')).toBeInTheDocument()
    expect(start.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ team: ['TechWriter'], suggested_team: ['TechWriter'] }))
  })

  it('plans again with the team the Manager changed, and holds the launch while a task is outside it', async () => {
    const planMutation = mutation({ mutateAsync: vi.fn().mockResolvedValueOnce(backendPlan).mockResolvedValueOnce({ ...backendPlan, tasks: [backendPlan.tasks[0]], suggested_team: ['BackendDeveloper', 'QA'] }) })
    const start = mutation({ mutateAsync: vi.fn().mockResolvedValue({ sprint_id: 23 }) })
    vi.mocked(usePlanSprint).mockReturnValue(planMutation as never)
    vi.mocked(useStartSprint).mockReturnValue(start as never)
    renderPlanner()
    await plan()

    fireEvent.click(screen.getByRole('checkbox', { name: /Technical Writer/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: /QA Engineer/ }))
    expect(screen.getByRole('note')).toHaveTextContent('plan again with this team')
    expect(screen.getByRole('button', { name: 'Launch Sprint' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }))
    await screen.findByText('Plan Preview')
    expect(screen.getByText(/Add the endpoint/)).toBeInTheDocument()
    expect(planMutation.mutateAsync).toHaveBeenLastCalledWith({
      company_id: 42, prompt: 'Document the project', scope_size: 'M', team: ['BackendDeveloper', 'QA'],
    })
    expect(screen.queryByText(/Document it$/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Launch Sprint' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Launch Sprint' }))
    expect(await screen.findByTestId('board')).toBeInTheDocument()
    expect(start.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ team: ['BackendDeveloper', 'QA'] }))
  })

  it('shows the team read-only in automatic mode, and launches with the suggestion unchanged and no team', async () => {
    const start = mutation({ mutateAsync: vi.fn().mockResolvedValue({ sprint_id: 23 }) })
    vi.mocked(usePlanSprint).mockReturnValue(mutation({ mutateAsync: vi.fn().mockResolvedValue({ ...docsPlan, team_selection: 'automatic' }) }) as never)
    vi.mocked(useStartSprint).mockReturnValue(start as never)
    renderPlanner()
    await plan()

    expect(screen.queryByRole('checkbox', { name: /Technical Writer/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Sprint team' })).not.toBeInTheDocument()
    expect(within(screen.getByLabelText('Team:')).getByText('Technical Writer')).toBeInTheDocument()
    expect(screen.getByText("Chosen automatically from the PO's suggestion.")).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Launch Sprint' }))
    expect(await screen.findByTestId('board')).toBeInTheDocument()
    const sent = start.mutateAsync.mock.calls[0][0]
    expect(sent.suggested_team).toEqual(['TechWriter'])
    expect(sent.team).toBeUndefined()
  })

  it('shows the team on the sprint page, or every hired role without one', () => {
    const { unmount } = render(<SprintTeamChips team={['BackendDeveloper', 'QA']} source="person" />)
    expect(screen.getByText('Backend Developer')).toBeInTheDocument()
    expect(screen.getByText("Chosen by a Manager, who changed the PO's suggestion.")).toBeInTheDocument()
    expect(screen.getByText('QA Engineer')).toBeInTheDocument()
    unmount()
    render(<SprintTeamChips team={null} source="all" />)
    expect(screen.getByText('every role the company hired')).toBeInTheDocument()
    expect(screen.getByText('No team chosen: every role the company hired.')).toBeInTheDocument()
  })
})
