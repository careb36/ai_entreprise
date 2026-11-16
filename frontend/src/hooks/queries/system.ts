import { useQuery } from '@tanstack/react-query'
import { useAppStore } from '../../store/useAppStore'
import { useAuthStore } from '../../store/useAuthStore'
import { QUERY_KEYS } from '../../services/queryKeys'
import {
  api,
  isClientError,
  type CompanyRole,
} from '../../services/api'

// The backend's capabilities, the daemon's status, the model catalogue and the signed-in user's role.
// Re-exported by hooks/useQueries.ts, which callers import.

export function useCapabilities() {
  return useQuery({
    queryKey: QUERY_KEYS.capabilities(),
    queryFn: api.capabilities,
    staleTime: 1000 * 60 * 5,
  })
}

/**
 * Whether the native workspace daemon answers. Polls every 10 s while it is
 * not connected; stops once it is (or when the endpoint rejects the client).
 */
export function useWorkspaceStatus() {
  return useQuery({
    queryKey: QUERY_KEYS.workspaceStatus(),
    queryFn: api.workspaceStatus,
    refetchInterval: query =>
      query.state.data?.connected === true || isClientError(query.state.error) ? false : 10_000,
  })
}

export function useModels() {
  return useQuery({
    queryKey: QUERY_KEYS.models(),
    queryFn: () => api.get<{ models: any[], orchestrator?: string }>('/v1/models'),
    staleTime: 1000 * 60 * 60, // 1 hour
  })
}

/** The signed-in user's role in the selected company, or null. */
export function useCurrentRole(): CompanyRole | null {
  const companyId = useAppStore(state => state.companyId)
  const memberships = useAuthStore(state => state.memberships)
  const membership = memberships.find(m => m.company_id === companyId)
  // What the backend checks: the own role raised by the member's teams.
  return membership?.effective_role ?? membership?.human_role ?? null
}
