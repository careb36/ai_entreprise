import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { standardsApi } from '../../services/standards'
import { QUERY_KEYS } from '../../services/queryKeys'
import {
  api,
  type AgentRunApprovalRequest,
  type AgentRunFilters,
} from '../../services/api'

// Agent runs and their evidence: verifications, approvals, the audit chain, the standards and the Reviewer's record.
// Re-exported by hooks/useQueries.ts, which callers import.

export function useAgentRuns(
  companyId: number | null,
  filters: AgentRunFilters = {},
) {
  return useQuery({
    queryKey: QUERY_KEYS.agentRuns(companyId ?? 0, filters),
    queryFn: () => api.getAgentRuns(companyId!, filters),
    enabled: companyId !== null,
  })
}

export function useAgentRun(companyId: number | null, runId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.agentRun(companyId ?? 0, runId ?? 0),
    queryFn: () => api.getAgentRun(runId!, companyId!),
    enabled: companyId !== null && runId !== null,
  })
}

export function useAgentRunVerifications(companyId: number | null, runId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.verifications(companyId ?? 0, runId ?? 0),
    queryFn: () => api.getAgentRunVerifications(runId!, companyId!),
    enabled: companyId !== null && runId !== null,
  })
}

export function useAgentRunApprovals(companyId: number | null, runId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.approvals(companyId ?? 0, runId ?? 0),
    queryFn: () => api.getAgentRunApprovals(runId!, companyId!),
    enabled: companyId !== null && runId !== null,
  })
}

export function useAuditVerification(externalRef: string | null, companyId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.auditVerification(companyId, externalRef ?? ''),
    queryFn: () => api.getAuditVerification(externalRef!, companyId!),
    enabled: externalRef !== null && companyId !== null,
  })
}

export function useVerifyAgentRun(companyId: number, runId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.verifyAgentRun(runId, companyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.agentRun(companyId, runId) })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.verifications(companyId, runId) })
      queryClient.invalidateQueries({ queryKey: ['agent-runs', companyId] })
      queryClient.invalidateQueries({ queryKey: ['audit-verification'] })
    },
  })
}

export function useApproveAgentRun(companyId: number, runId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (request: AgentRunApprovalRequest) => api.approveAgentRun(runId, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.approvals(companyId, runId) })
      queryClient.invalidateQueries({ queryKey: ['agent-runs', companyId] })
      queryClient.invalidateQueries({ queryKey: ['audit-verification'] })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.reviewerRecord(companyId) })
    },
  })
}

export function useStandards() {
  return useQuery({ queryKey: ['standards'] as const, queryFn: standardsApi.list, staleTime: Infinity })
}

/** How the Reviewer agent's suggestions compared with the decisions recorded on the company's runs. */
export function useReviewerRecord(companyId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.reviewerRecord(companyId ?? 0),
    queryFn: () => api.getReviewerRecord(companyId!),
    enabled: companyId !== null,
  })
}
