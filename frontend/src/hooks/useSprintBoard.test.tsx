import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSprintBoard } from './useQueries'
import { api, ApiError } from '../services/api'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import type { ReactNode } from 'react'

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

describe('sprint board errors', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ activeSprintId: 999 })
  })

  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it.each([400, 401, 403, 404, 422, 429])('does not retry or poll HTTP %s', async status => {
    const fetchBoard = vi.spyOn(api, 'getSprintBoard').mockRejectedValue(new ApiError(status, 'failed'))
    const { result } = renderHook(() => useSprintBoard(999), { wrapper })
    await waitFor(() => expect(result.current.isError).toBe(true))
    vi.useFakeTimers()
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(fetchBoard).toHaveBeenCalledTimes(1)
  })

  it('clears a persisted missing selection only once across multiple observers', async () => {
    vi.spyOn(api, 'getSprintBoard').mockRejectedValue(new ApiError(404, 'missing'))
    const setActiveSprint = vi.spyOn(useAppStore.getState(), 'setActiveSprint')
    renderHook(() => {
      useSprintBoard(999)
      useSprintBoard(999)
    }, { wrapper })
    await waitFor(() => expect(useAppStore.getState().activeSprintId).toBeNull())
    expect(setActiveSprint).toHaveBeenCalledTimes(1)
    expect(JSON.parse(localStorage.getItem('fabrique-app-storage')!).state.activeSprintId).toBeNull()
  })

  it('does not clear a newer selection when an old board returns 404', async () => {
    let reject!: (error: Error) => void
    vi.spyOn(api, 'getSprintBoard').mockReturnValue(new Promise((_, rejectPromise) => { reject = rejectPromise }))
    const { result } = renderHook(() => useSprintBoard(999), { wrapper })
    act(() => useAppStore.getState().setActiveSprint(7))
    act(() => reject(new ApiError(404, 'missing')))
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(useAppStore.getState().activeSprintId).toBe(7)
  })

  it('retains the selection and retries transient failures once', async () => {
    queryClient.setDefaultOptions({
      ...queryClient.getDefaultOptions(),
      queries: { ...queryClient.getDefaultOptions().queries, retryDelay: 0 },
    })
    const fetchBoard = vi.spyOn(api, 'getSprintBoard').mockRejectedValue(new ApiError(503, 'unavailable'))
    const { result } = renderHook(() => useSprintBoard(999), { wrapper })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(fetchBoard).toHaveBeenCalledTimes(2)
    expect(useAppStore.getState().activeSprintId).toBe(999)
  })
})
