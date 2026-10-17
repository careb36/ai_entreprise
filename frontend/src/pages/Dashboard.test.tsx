import { fireEvent, render, screen } from '@testing-library/react'
import i18n from 'i18next'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Dashboard from './Dashboard'
import { useAnalyticsSummary, useHealth, useSprintBoard } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'

const mocks = vi.hoisted(() => ({
  agentStore: {
    agentStates: {},
    isConnected: true,
    connect: vi.fn(),
    disconnect: vi.fn(),
  },
  useSprintBoard: vi.fn(),
  useAnalyticsSummary: vi.fn(),
  useHealth: vi.fn(),
  mutation: {
    isPending: false,
    error: null,
    mutateAsync: vi.fn(),
    reset: vi.fn(),
  },
}))

vi.mock('../hooks/useQueries', () => ({
  useSprintBoard: mocks.useSprintBoard,
  useAnalyticsSummary: mocks.useAnalyticsSummary,
  useHealth: mocks.useHealth,
  useStartSprint: () => mocks.mutation,
  useInitCompany: () => mocks.mutation,
  usePlanSprint: () => mocks.mutation,
  usePlanSelfImprovementSprint: () => mocks.mutation,
}))

vi.mock('../store/agentStore', () => ({
  useAgentStore: <T,>(selector: (state: typeof mocks.agentStore) => T) => selector(mocks.agentStore),
}))

const mockedUseSprintBoard = vi.mocked(useSprintBoard)
const mockedUseAnalyticsSummary = vi.mocked(useAnalyticsSummary)
const mockedUseHealth = vi.mocked(useHealth)

const board = {
  sprint: { id: 8, sprint_name: 'Dashboard sprint', status: 'active', scope_size: 'S' },
  columns: {
    todo: [{}],
    in_progress: [{}],
    in_review: [],
    done: [{}],
    blocked: [],
  },
  total: 3,
}

function mockDashboardData() {
  mockedUseSprintBoard.mockReturnValue({
    data: board,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as never)
  mockedUseAnalyticsSummary.mockReturnValue({
    data: { health_score: 90 },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as never)
  mockedUseHealth.mockReturnValue({
    data: { status: 'healthy' },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as never)
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  )
}

describe('Dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.getState().setCompany(42, 'Le Fabrique')
    useAppStore.getState().setActiveSprint(8)
    mockDashboardData()
  })

  it('shows a localized, accessible breakdown of sprint task status', () => {
    renderDashboard()

    expect(screen.getByRole('heading', { name: i18n.t('dashboard.taskStatusBreakdown') })).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: i18n.t('kanban.status.done') })).toHaveAttribute('aria-valuenow', '1')
    expect(screen.getByRole('progressbar', { name: i18n.t('kanban.status.done') })).toHaveAttribute('aria-valuemax', '3')
    expect(screen.getByRole('progressbar', { name: i18n.t('kanban.status.done') })).toHaveAttribute('aria-valuetext', '1 of 3 tasks')
  })

  it('announces loading and offers a retry when sprint data fails', () => {
    const refetch = vi.fn()
    mockedUseSprintBoard.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch,
    } as never)
    const { rerender } = renderDashboard()

    expect(screen.getByText(i18n.t('dashboard.loadingSprint')).closest('[role="status"]')).not.toBeNull()

    mockedUseSprintBoard.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch,
    } as never)
    rerender(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    )

    expect(screen.getByRole('alert')).toHaveTextContent(i18n.t('dashboard.sprintUnavailable'))
    fireEvent.click(screen.getByRole('button', { name: i18n.t('dashboard.retry') }))
    expect(refetch).toHaveBeenCalledOnce()
  })

  it('shows a retryable analytics error instead of silently omitting the health score', () => {
    const refetch = vi.fn()
    mockedUseAnalyticsSummary.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch,
    } as never)
    renderDashboard()

    expect(screen.getByRole('alert')).toHaveTextContent(i18n.t('dashboard.analyticsUnavailable'))
    fireEvent.click(screen.getByRole('button', { name: i18n.t('dashboard.retry') }))
    expect(refetch).toHaveBeenCalledOnce()
  })
})
