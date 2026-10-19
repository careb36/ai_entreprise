import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GovernancePanel } from './GovernancePanel'
import { useAgentRuns, useCurrentRole, useInbox, useUpdateBacklogItem } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'

vi.mock('../hooks/useQueries', () => ({
  useAgentRuns: vi.fn(),
  useCurrentRole: vi.fn(),
  useInbox: vi.fn(),
  useUpdateBacklogItem: vi.fn(),
}))

const mockedUseAgentRuns = vi.mocked(useAgentRuns)
const mockedUseCurrentRole = vi.mocked(useCurrentRole)
const mockedUseInbox = vi.mocked(useInbox)
const mockedUseUpdateBacklogItem = vi.mocked(useUpdateBacklogItem)

const INBOX = {
  blocked_tasks: [
    {
      id: 7, title: 'Create testing strategy document', assignee: 'QA', sprint_id: 1, sprint_name: 'Sprint 0 — Foundations',
      sprint_status: 'completed', reason: 'Pre-review quality gate failed after 2 rework(s) — blocked for manual triage: Too short',
      frozen: false, updated_at: '2026-10-19T04:39:00',
    },
    {
      id: 8, title: 'Frozen one', assignee: 'Developer', sprint_id: 1, sprint_name: 'Sprint 0 — Foundations',
      sprint_status: 'completed', reason: 'freeze after sleep-induced churn', frozen: true, updated_at: null,
    },
  ],
  proposals: [{ id: 3, role: 'Developer', title: 'Cover edge cases', proposed_by: 'ScrumMaster', source_sprint_id: 1, created_at: null }],
  pending_runs: 1,
  total: 4,
}

describe('GovernancePanel', () => {
  const mutate = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ companyId: 42, companyName: 'Example', activeSprintId: null })
    mockedUseAgentRuns.mockReturnValue({
      data: {
        agent_runs: [
          { id: 1, external_ref: 'needs-review', producer: 'producer-a', agent_role: 'Developer', latest_approval: null },
          { id: 2, external_ref: 'already-reviewed', producer: 'producer-b', agent_role: 'QA', latest_approval: { decision: 'approved' } },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as never)
    mockedUseInbox.mockReturnValue({ data: INBOX, isLoading: false, isError: false, refetch: vi.fn() } as never)
    mockedUseCurrentRole.mockReturnValue('Manager')
    mockedUseUpdateBacklogItem.mockReturnValue({ mutate, isPending: false, isError: false, error: null } as never)
  })

  it('lists only runs without a latest approval and links to their evidence', () => {
    render(<MemoryRouter><GovernancePanel /></MemoryRouter>)

    expect(screen.getByText('needs-review')).toBeInTheDocument()
    expect(screen.queryByText('already-reviewed')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Review evidence/ })).toHaveAttribute('href', '/evidence/1')
    expect(screen.getByRole('heading', { name: 'Review' })).toBeInTheDocument()
  })

  it('shows the tasks blocked for a person with their reason, and a Manager can send one back', () => {
    render(<MemoryRouter><GovernancePanel /></MemoryRouter>)

    const blocked = screen.getByRole('region', { name: /Tasks blocked until someone acts/ })
    expect(within(blocked).getByRole('link', { name: 'Create testing strategy document' })).toHaveAttribute('href', '/sprints/1/tasks/7')
    expect(within(blocked).getByText(/blocked for manual triage: Too short/)).toBeInTheDocument()
    expect(within(blocked).getByText(/frozen by an operator/)).toBeInTheDocument()
    // Only the task the agents gave up on gets the button; a frozen one is the operator's.
    const buttons = within(blocked).getAllByRole('button', { name: 'Send back to work' })
    expect(buttons).toHaveLength(1)

    fireEvent.click(buttons[0])

    expect(mutate).toHaveBeenCalledWith({ itemId: 7, data: { status: 'todo', comment: 'Sent back to work by a person.' } })
  })

  it('links proposals to the procedures page and counts undecided runs', () => {
    render(<MemoryRouter><GovernancePanel /></MemoryRouter>)

    const proposals = screen.getByRole('region', { name: /Procedure proposals waiting for a decision/ })
    expect(within(proposals).getByText('Cover edge cases')).toBeInTheDocument()
    expect(within(proposals).getByText('Developer · proposed by ScrumMaster')).toBeInTheDocument()
    expect(within(proposals).getByRole('link', { name: 'Decide' })).toHaveAttribute('href', '/company/procedures')
    expect(screen.getByRole('heading', { name: /Runs awaiting a decision/ })).toHaveTextContent('1')
  })

  it('offers no action to a Viewer and says so when nothing waits', () => {
    mockedUseCurrentRole.mockReturnValue('Viewer')
    mockedUseInbox.mockReturnValue({
      data: { blocked_tasks: [], proposals: [], pending_runs: 0, total: 0 }, isLoading: false, isError: false, refetch: vi.fn(),
    } as never)
    render(<MemoryRouter><GovernancePanel /></MemoryRouter>)

    expect(screen.queryByRole('button', { name: 'Send back to work' })).not.toBeInTheDocument()
    expect(screen.getByText('No task is waiting for a person.')).toBeInTheDocument()
    expect(screen.getByText('No proposal is waiting.')).toBeInTheDocument()
  })

  it('reports a load error with a refresh button', () => {
    const refetch = vi.fn()
    mockedUseInbox.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error('boom'), refetch } as never)
    render(<MemoryRouter><GovernancePanel /></MemoryRouter>)

    expect(screen.getByRole('alert')).toHaveTextContent(/Could not load what needs you/)
    fireEvent.click(within(screen.getByRole('alert')).getByRole('button'))
    expect(refetch).toHaveBeenCalled()
  })
})
