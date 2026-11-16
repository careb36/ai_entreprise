import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { QUERY_KEYS } from '../../services/queryKeys'
import {
  api,
} from '../../services/api'

// The company's quotas and their usage.
// Re-exported by hooks/useQueries.ts, which callers import.

export function useQuotas(companyId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.quotas(companyId || 0),
    queryFn: () => api.getQuotas(companyId!),
    enabled: !!companyId,
  })
}

export function useResetQuota() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (buId: number) => api.resetQuota(buId),
    onSuccess: (_data, _buId) => {
      // Invalidate all quota queries so the bar refreshes immediately.
      queryClient.invalidateQueries({ queryKey: ['enterprise'] })
    },
  })
}

export function useQuotaUsageHistory(companyId: number | null, days = 7) {
  return useQuery({
    queryKey: QUERY_KEYS.quotaHistory(companyId || 0, days),
    queryFn: () => api.getQuotaUsageHistory(companyId!, days),
    enabled: !!companyId,
  })
}
