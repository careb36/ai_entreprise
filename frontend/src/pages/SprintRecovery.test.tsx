import { render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Dashboard from './Dashboard'
import { AnalyticsDashboard } from '../components/AnalyticsDashboard'
import { api, ApiError } from '../services/api'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAgentStore } from '../store/agentStore'

function Analytics() {
  const sprintId = useAppStore(state => state.activeSprintId)
  return <AnalyticsDashboard sprintId={sprintId ?? 0} />
}

describe('stale persisted sprint recovery', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ activeSprintId: 999, companyId: 42 })
    vi.spyOn(api, 'getSprintBoard').mockRejectedValue(new ApiError(404, 'missing'))
    vi.spyOn(api, 'getAnalyticsSummary').mockRejectedValue(new ApiError(404, 'missing'))
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'healthy' } as never)
    vi.spyOn(useAgentStore.getState(), 'connect').mockImplementation(() => {})
    vi.spyOn(useAgentStore.getState(), 'disconnect').mockImplementation(() => {})
  })
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it.each([
    ['Dashboard', Dashboard],
    ['Analytics', Analytics],
  ] as const)('%s clears a persisted missing sprint and exits the error state', async (_, Page) => {
    render(<QueryClientProvider client={queryClient}><MemoryRouter><Page /></MemoryRouter></QueryClientProvider>)
    await waitFor(() => expect(useAppStore.getState().activeSprintId).toBeNull())
    expect(JSON.parse(localStorage.getItem('fabrique-app-storage')!).state.activeSprintId).toBeNull()
    expect(screen.getAllByText('No active sprint').length).toBeGreaterThan(0)
    expect(screen.queryByText('Failed to load analytics')).not.toBeInTheDocument()
    expect(api.getSprintBoard).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('link', { name: 'View live stream' })).not.toBeInTheDocument()
  })

  it('links an existing sprint to its Live view', async () => {
    vi.mocked(api.getSprintBoard).mockResolvedValue({
      sprint: { sprint_name: 'Sprint 999', status: 'active', scope_size: 'M' },
      columns: {}, total: 0,
    } as never)
    render(<QueryClientProvider client={queryClient}><MemoryRouter><Dashboard /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByRole('link', { name: 'Live view' })).toHaveAttribute('href', '/workspace/999')
  })
})
