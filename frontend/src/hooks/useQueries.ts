import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { useAppStore } from '../store/useAppStore'
import {
  api,
  ApiError,
  isClientError,
  type AgentRunApprovalRequest,
  type AgentRunStatus,
  type BacklogItem,
  type InitCompanyRequest,
} from '../services/api'

// ── Keys ─────────────────────────────────────────────────────────────────────

export const QUERY_KEYS = {
  sprintBoard: (id: number) => ['sprint', id, 'board'] as const,
  quotas: (companyId: number) => ['enterprise', companyId, 'quotas'] as const,
  analytics: (sprintId: number) => ['analytics', sprintId] as const,
  orchestrationHealth: (companyId: number, weeks: number) => ['orchestration_health', companyId, weeks] as const,
  tickets: () => ['tickets'] as const,
  memory: (query: string) => ['memory', query] as const,
  models: () => ['models'] as const,
  agentRuns: (companyId: number, producer?: string, status?: AgentRunStatus, limit?: number) =>
    ['agent-runs', companyId, producer ?? '', status ?? '', limit ?? 50] as const,
  agentRun: (companyId: number, runId: number) => ['agent-run', companyId, runId] as const,
  verifications: (companyId: number, runId: number) => ['agent-run', companyId, runId, 'verifications'] as const,
  approvals: (companyId: number, runId: number) => ['agent-run', companyId, runId, 'approvals'] as const,
  auditVerification: (externalRef: string) => ['audit-verification', externalRef] as const,
}

// ── Enterprise & Company ─────────────────────────────────────────────────────

export function useAgentRuns(
  companyId: number | null,
  filters: { producer?: string; status?: AgentRunStatus; limit?: number } = {},
) {
  return useQuery({
    queryKey: QUERY_KEYS.agentRuns(companyId ?? 0, filters.producer, filters.status, filters.limit),
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

export function useAuditVerification(externalRef: string | null) {
  return useQuery({
    queryKey: QUERY_KEYS.auditVerification(externalRef ?? ''),
    queryFn: () => api.getAuditVerification(externalRef!),
    enabled: externalRef !== null,
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
    },
  })
}

export function useModels() {
  return useQuery({
    queryKey: QUERY_KEYS.models(),
    queryFn: () => api.get<{ models: any[], orchestrator?: string }>('/v1/models'),
    staleTime: 1000 * 60 * 60, // 1 hour
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

// ── Sprints & Backlog ─────────────────────────────────────────────────────────

export function useStartSprint() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: any) => api.post<{ sprint_id: number }>('/v1/start_sprint', data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sprint'] })
    },
  })
}

export function usePlanSprint() {
  return useMutation({
    mutationFn: (data: Parameters<typeof api.planSprint>[0]) => api.planSprint(data),
  })
}

export function usePlanSelfImprovementSprint() {
  return useMutation({
    mutationFn: (data: Parameters<typeof api.planSelfImprovementSprint>[0]) => api.planSelfImprovementSprint(data),
  })
}

export function useSprintBoard(sprintId: number | null) {
  const query = useQuery({
    queryKey: QUERY_KEYS.sprintBoard(sprintId || 0),
    queryFn: () => api.getSprintBoard(sprintId!),
    enabled: !!sprintId,
    staleTime: 1000 * 60 * 5, // 5 minutes
    refetchInterval: query => isClientError(query.state.error) ? false : 10_000,
  })

  useEffect(() => {
    if (query.error instanceof ApiError && query.error.status === 404) {
      const state = useAppStore.getState()
      if (sprintId !== null && state.activeSprintId === sprintId) {
        state.setActiveSprint(null)
      }
    }
  }, [sprintId, query.error])

  return query
}

export function useUpdateBacklogItem() {
  const queryClient = useQueryClient()
  
  return useMutation({
    mutationFn: ({ itemId, data }: { itemId: number, data: Parameters<typeof api.updateBacklogItem>[1] }) => 
      api.updateBacklogItem(itemId, data),
    onSuccess: (updatedItem: BacklogItem) => {
      if (updatedItem.sprint_id) {
        queryClient.invalidateQueries({ queryKey: QUERY_KEYS.sprintBoard(updatedItem.sprint_id) })
      }
    },
  })
}

// ── Enterprise & Analytics ───────────────────────────────────────────────────

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

export function useAnalyticsSummary(sprintId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.analytics(sprintId || 0),
    queryFn: () => api.getAnalyticsSummary(sprintId!),
    enabled: !!sprintId,
  })
}

export function useOrchestrationHealth(companyId: number | null, weeks = 8) {
  return useQuery({
    queryKey: QUERY_KEYS.orchestrationHealth(companyId || 0, weeks),
    queryFn: () => api.getOrchestrationHealth(companyId!, weeks),
    enabled: !!companyId,
  })
}

// ── Agents & Memory ──────────────────────────────────────────────────────────

export function useGenerate() {
  return useMutation({
    mutationFn: api.generate,
  })
}

export function useSearchMemory(query: string, companyId?: number) {
  return useQuery({
    queryKey: QUERY_KEYS.memory(query),
    queryFn: () => api.searchMemory(query, companyId),
    enabled: query.length > 2,
  })
}

// ── Support ──────────────────────────────────────────────────────────────────

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    refetchInterval: query => isClientError(query.state.error) ? false : 30_000,
  })
}

export function useTickets() {
  return useQuery({
    queryKey: QUERY_KEYS.tickets(),
    queryFn: api.getTickets,
  })
}

export function useReportBug() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.reportBug,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.tickets() })
    },
  })
}
