import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OVERRIDE_INVALIDATES, managerApi, useOverrideManagerDecision } from './manager'

describe('useOverrideManagerDecision', () => {
  afterEach(() => vi.restoreAllMocks())

  it('reads again every view an override can change: boards, procedures, runs and approvals, the record', async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    vi.spyOn(managerApi, 'override').mockResolvedValue({ id: 5, outcome: 'waits_for_a_person' })
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
    const { result } = renderHook(() => useOverrideManagerDecision(), { wrapper })
    await act(async () => { await result.current.mutateAsync(5) })
    await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(OVERRIDE_INVALIDATES.length))
    const keys = invalidate.mock.calls.map(([filters]) => (filters as { queryKey: string[] }).queryKey[0])
    expect(keys).toEqual(['inbox', 'company', 'sprint', 'sprints', 'procedures', 'agent-runs', 'agent-run', 'audit-verification', 'manager-record'])
  })
})
