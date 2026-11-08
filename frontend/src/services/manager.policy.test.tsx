import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { useSaveManagerPolicy } from './manager'

describe('useSaveManagerPolicy', () => {
  it('shows the stored policy the PUT answered, without waiting for another GET', async () => {
    const stored = { company_id: 3, rules: ['merge_local'], updated_by: 'owner', updated_at: '2026-11-08T22:00:00' }
    vi.spyOn(api, 'put').mockResolvedValue(stored as never)
    vi.spyOn(api, 'get').mockRejectedValue(new Error('offline'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useSaveManagerPolicy(3), { wrapper })
    await act(async () => {
      result.current.mutate({ rules: ['merge_local'], max_files: 10, max_criteria: 5, max_files_high_risk: 3, publish_enabled: false })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(client.getQueryData(['company', 3, 'manager-policy'])).toEqual(stored)
  })
})
