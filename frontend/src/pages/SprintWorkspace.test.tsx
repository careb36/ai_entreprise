import { useEffect } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SprintWorkspace } from './SprintWorkspace'
import { api, ApiError, type SprintBoard } from '../services/api'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { QUERY_KEYS } from '../hooks/useQueries'

const disconnect = vi.hoisted(() => vi.fn())

vi.mock('../components/GlassRoom', () => ({
  default: ({ sprintId }: { sprintId: string }) => {
    useEffect(() => () => { disconnect() }, [sprintId])
    return <div>Stream for sprint {sprintId}</div>
  },
}))

const board = { sprint: { id: 8, company_id: 1 }, columns: {}, total: 0 } as SprintBoard

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
  const defaultOptions = queryClient.getDefaultOptions()

  beforeEach(() => {
    queryClient.clear()
    disconnect.mockClear()
    useAppStore.setState({ companyId: 1, activeSprintId: 7 })
  })
  afterEach(() => {
    queryClient.clear()
    queryClient.setDefaultOptions(defaultOptions)
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
    await act(async () => { resolve(board) })
    await waitFor(() => expect(useAppStore.getState().activeSprintId).toBe(8))
    expect(screen.getByText('Stream for sprint 8')).toBeInTheDocument()
  })

  it('clears a forbidden selection and explains lost access without Retry', async () => {
    useAppStore.setState({ activeSprintId: 999 })
    vi.spyOn(api, 'getSprintBoard').mockRejectedValue(new ApiError(403, 'forbidden'))
    setup('999')
    expect(await screen.findByRole('alert')).toHaveTextContent('You no longer have access to sprint #999.')
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Stream for sprint/)).not.toBeInTheDocument()
    await waitFor(() => expect(useAppStore.getState().activeSprintId).toBeNull())
  })

  it.each([new ApiError(503, 'unavailable'), new TypeError('Failed to fetch')])(
    'keeps the live view mounted after a background refresh fails: %s',
    async error => {
      queryClient.setDefaultOptions({ queries: { ...defaultOptions.queries, retry: false } })
      const fetchBoard = vi.spyOn(api, 'getSprintBoard').mockResolvedValueOnce(board).mockRejectedValueOnce(error).mockResolvedValue(board)
      setup('8')
      const stream = await screen.findByText('Stream for sprint 8')
      await act(async () => {
        await queryClient.refetchQueries({ queryKey: QUERY_KEYS.sprintBoard(8) })
      })
      const alert = await screen.findByRole('alert')
      expect(screen.getByText('Stream for sprint 8')).toBe(stream)
      expect(disconnect).not.toHaveBeenCalled()
      expect(alert).toHaveTextContent('Failed to refresh this sprint. Showing the last loaded view.')
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
      await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
      expect(fetchBoard).toHaveBeenCalledTimes(3)
      expect(screen.getByText('Stream for sprint 8')).toBe(stream)
      expect(disconnect).not.toHaveBeenCalled()
    },
  )

  it.each([
    { status: 403, message: 'You no longer have access to sprint #8.' },
    { status: 404, message: 'Sprint #8 was not found.' },
  ])('does not offer Retry for a cached board returning HTTP $status', async ({ status, message }) => {
    vi.spyOn(api, 'getSprintBoard').mockResolvedValueOnce(board).mockRejectedValue(new ApiError(status, 'inaccessible'))
    setup('8')
    await screen.findByText('Stream for sprint 8')
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: QUERY_KEYS.sprintBoard(8) })
    })
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
    expect(useAppStore.getState().activeSprintId).toBeNull()
  })

  it('offers Retry when the initial load fails without cached data', async () => {
    queryClient.setDefaultOptions({ queries: { ...defaultOptions.queries, retry: false } })
    vi.spyOn(api, 'getSprintBoard').mockRejectedValueOnce(new ApiError(503, 'unavailable')).mockResolvedValue(board)
    setup('8')
    expect(await screen.findByRole('alert')).toHaveTextContent('Failed to load sprint #8.')
    expect(screen.queryByText(/Stream for sprint/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Stream for sprint 8')).toBeInTheDocument()
  })

  it.each([2, null])('does not select a URL sprint from another company when companyId is %s', async companyId => {
    useAppStore.setState({ companyId, companyName: 'Selected company' })
    const previousState = useAppStore.getState()
    const persisted = localStorage.getItem('fabrique-app-storage')
    const setActiveSprint = vi.spyOn(previousState, 'setActiveSprint')
    vi.spyOn(api, 'getSprintBoard').mockResolvedValue(board)
    setup('8')
    expect(await screen.findByText('Stream for sprint 8')).toBeInTheDocument()
    expect(setActiveSprint).not.toHaveBeenCalled()
    expect(useAppStore.getState()).toEqual(previousState)
    expect(localStorage.getItem('fabrique-app-storage')).toBe(persisted)
    expect(screen.getByRole('alert')).toHaveTextContent('This sprint belongs to another company. This view is read-only.')
    expect(screen.getByRole('link', { name: 'Select company' })).toHaveAttribute('href', '/companies/select')
  })

  it.each(['nope', '8junk', '0', '-1', '1.5', '9007199254740992'])('rejects malformed sprint ID %s without a request', id => {
    const fetchBoard = vi.spyOn(api, 'getSprintBoard')
    setup(id)
    expect(screen.getByRole('alert')).toHaveTextContent(`Invalid sprint ID: ${id}`)
    expect(fetchBoard).not.toHaveBeenCalled()
    expect(useAppStore.getState().activeSprintId).toBe(7)
  })
})
