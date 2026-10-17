import { act, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SprintWorkspace } from './SprintWorkspace'
import { api, ApiError, type SprintBoard } from '../services/api'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'

vi.mock('../components/GlassRoom', () => ({
  default: ({ sprintId }: { sprintId: string }) => <div>Stream for sprint {sprintId}</div>,
}))

function setup(id: string) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/workspace/${id}`]}>
        <Routes><Route path="/workspace/:id" element={<SprintWorkspace />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('workspace sprint validation', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ activeSprintId: 7 })
  })
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('does not persist or stream a missing URL sprint, even with a different selection', async () => {
    vi.spyOn(api, 'getSprintBoard').mockRejectedValue(new ApiError(404, 'missing'))
    setup('999')
    expect(await screen.findByRole('alert')).toHaveTextContent('Sprint #999 was not found')
    expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('href', '/dashboard')
    expect(screen.queryByText(/Stream for sprint/)).not.toBeInTheDocument()
    expect(useAppStore.getState().activeSprintId).toBe(7)
    expect(JSON.parse(localStorage.getItem('fabrique-app-storage')!).state.activeSprintId).toBe(7)
    expect(api.getSprintBoard).toHaveBeenCalledWith(999)
  })

  it('waits for URL sprint validation before persisting or connecting', async () => {
    let resolve!: (board: SprintBoard) => void
    vi.spyOn(api, 'getSprintBoard').mockReturnValue(new Promise(resolvePromise => { resolve = resolvePromise }))
    setup('8')
    expect(useAppStore.getState().activeSprintId).toBe(7)
    expect(screen.queryByText(/Stream for sprint/)).not.toBeInTheDocument()
    await act(async () => { resolve({ sprint: { id: 8 }, columns: {}, total: 0 } as SprintBoard) })
    await waitFor(() => expect(useAppStore.getState().activeSprintId).toBe(8))
    expect(screen.getByText('Stream for sprint 8')).toBeInTheDocument()
  })

  it.each(['nope', '8junk', '0', '-1', '1.5', '9007199254740992'])('rejects malformed sprint ID %s without a request', id => {
    const fetchBoard = vi.spyOn(api, 'getSprintBoard')
    setup(id)
    expect(screen.getByRole('alert')).toHaveTextContent(`Invalid sprint ID: ${id}`)
    expect(fetchBoard).not.toHaveBeenCalled()
    expect(useAppStore.getState().activeSprintId).toBe(7)
  })
})
