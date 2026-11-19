import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import RunWindowBadge from './RunWindowBadge'
import { useCurrentRole } from '../hooks/useQueries'
import { queryClient } from '../lib/queryClient'
import { api } from '../services/api'
import type { SprintState } from '../services/workControl'

vi.mock('../hooks/useQueries', () => ({ useCurrentRole: vi.fn() }))

const WAITING: SprintState = {
  id: 9,
  company_id: 4,
  sprint_name: 'Night sprint',
  status: 'active',
  start_date: '2026-11-24',
  scope_size: 'S',
  item_limit: 4,
  urgent: false,
  run_window: {
    waiting: true,
    until: '2026-11-25T04:00:00+00:00',
    until_local: '2026-11-25T01:00:00-03:00',
    timezone: 'America/Montevideo',
    urgent: false,
  },
}

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
}

describe('the run window on the board', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('tells a Viewer when the work starts, without the urgent toggle', () => {
    vi.mocked(useCurrentRole).mockReturnValue('Viewer')
    wrap(<RunWindowBadge sprint={WAITING} />)
    expect(screen.getByRole('status')).toHaveTextContent(/Waiting for the run window until .+ \(local\)/)
    expect(screen.getByRole('status')).toHaveAttribute('title', 'Starts at 2026-11-25T01:00:00-03:00 (America/Montevideo)')
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('lets a Manager mark the sprint urgent', async () => {
    vi.mocked(useCurrentRole).mockReturnValue('Manager')
    const set = vi.spyOn(api, 'setSprintUrgent').mockResolvedValue({ sprint_id: 9, urgent: true, released_tasks: 2, run_window: null })
    wrap(<RunWindowBadge sprint={WAITING} />)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Urgent' }))
    await waitFor(() => expect(set).toHaveBeenCalledWith(9, true))
  })

  it('shows nothing to a Viewer when nothing waits and the sprint is not urgent', () => {
    vi.mocked(useCurrentRole).mockReturnValue('Viewer')
    const { container } = wrap(<RunWindowBadge sprint={{ ...WAITING, run_window: null }} />)
    expect(container).toBeEmptyDOMElement()
  })
})
