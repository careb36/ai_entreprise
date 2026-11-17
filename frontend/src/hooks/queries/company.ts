import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { QUERY_KEYS } from '../../services/queryKeys'
import {
  api,
  type MessageFilters,
  type InitCompanyRequest,
} from '../../services/api'

// The company: its inbox, procedures, the conversation between roles, setup, members, the GitHub binding and lineage.
// Re-exported by hooks/useQueries.ts, which callers import.

/**
 * What the company needs a person for: blocked tasks, proposals, undecided
 * runs. Polled, so the navigation badge follows the agents' work.
 */
export function useInbox(companyId: number | null, refreshMs: number | false = 30_000) {
  return useQuery({
    queryKey: QUERY_KEYS.inbox(companyId ?? 0),
    queryFn: () => api.getInbox(companyId!),
    enabled: companyId !== null,
    refetchInterval: refreshMs,
  })
}

export function useProcedures(companyId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.procedures(companyId ?? 0),
    queryFn: () => api.getProcedures(companyId!),
    enabled: companyId !== null,
  })
}

export function useCreateProcedure() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.createProcedure,
    onSuccess: (_result, { company_id }) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.procedures(company_id) })
    },
  })
}

export function useDecideProcedure(companyId: number | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: 'adopt' | 'reject' | 'retire' }) =>
      api.decideProcedure(id, decision),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.procedures(companyId ?? 0) })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.inbox(companyId ?? 0) })
    },
  })
}

/** The conversation between roles; refreshed while the sprint is running. */
export function useAgentMessages(companyId: number | null, filters: MessageFilters = {}, refreshMs: number | false = false) {
  return useQuery({
    queryKey: QUERY_KEYS.messages(companyId ?? 0, filters),
    queryFn: () => api.getMessages(companyId!, filters),
    enabled: companyId !== null,
    refetchInterval: refreshMs,
  })
}

export function useDiscoverTeam() {
  return useMutation({
    mutationFn: ({ companyId, projectPath }: { companyId: number | null, projectPath: string }) => 
      api.discoverTeam(companyId, projectPath),
  })
}

export function useInitCompany() {
  return useMutation({
    mutationFn: (data: InitCompanyRequest) => api.initCompany(data),
  })
}

export function useCreateGitHubBinding() {
  return useMutation({ mutationFn: api.createGitHubBinding })
}

export function useBackfillGitHub() {
  return useMutation({ mutationFn: api.backfillGitHub })
}

export function useLineage(filePath: string, companyId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.lineage(companyId, filePath),
    queryFn: () => api.getLineage(filePath, companyId),
    enabled: filePath.trim().length > 0,
  })
}
