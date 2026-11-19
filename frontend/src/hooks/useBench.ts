import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'

/** The bench's committed scorecards, newest first (any signed-in caller). */
export function useBenchScorecards() {
  return useQuery({
    queryKey: ['bench-scorecards'] as const,
    queryFn: () => api.getBenchScorecards(),
  })
}
