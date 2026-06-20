import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  api,
  type BacklogItem,
  type InitCompanyRequest,
  type LoginResponse,
  type RegisterRequest,
} from '../services/api'
import { useAuthStore } from '../store/authStore'

// ── Keys ─────────────────────────────────────────────────────────────────────

export const QUERY_KEYS = {
  sprintBoard: (id: number) => ['sprint', id, 'board'] as const,
  quotas: (companyId: number) => ['enterprise', companyId, 'quotas'] as const,
  analytics: (sprintId: number) => ['analytics', sprintId] as const,
  tickets: () => ['tickets'] as const,
  memory: (query: string) => ['memory', query] as const,
  models: () => ['models'] as const,
  pendingApprovals: () => ['governance', 'pending'] as const,
}

// ── Enterprise & Company ─────────────────────────────────────────────────────

export function usePendingApprovals() {
  return useQuery({
    queryKey: QUERY_KEYS.pendingApprovals(),
    queryFn: () => api.get<{ pending_approvals: any[] }>('/v1/pending_approvals'),
    refetchInterval: 30_000, // Check every 30s
  })
}

export function useApproveTask() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ taskId, decision, approverName, feedback }: { taskId: number, decision: 'approved' | 'rejected', approverName: string, feedback?: string }) => 
      api.approveTask(taskId, approverName, decision, feedback),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.pendingApprovals() })
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
  return useQuery({
    queryKey: QUERY_KEYS.sprintBoard(sprintId || 0),
    queryFn: () => api.getSprintBoard(sprintId!),
    enabled: !!sprintId,
    staleTime: 1000 * 60 * 5, // 5 minutes
    refetchInterval: 10_000,   // Auto-poll every 10s while agents work
  })
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

export function useAnalyticsSummary(sprintId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.analytics(sprintId || 0),
    queryFn: () => api.getAnalyticsSummary(sprintId!),
    enabled: !!sprintId,
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
    refetchInterval: 30000, // Check every 30s
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

// ── Auth ──────────────────────────────────────────────────────────────────────

export function useLogin() {
  const setAuth = useAuthStore((s) => s.setAuth)
  return useMutation<LoginResponse, Error, { email: string; password: string }>({
    mutationFn: ({ email, password }) => api.login(email, password),
    onSuccess: (data) => {
      setAuth(data.access_token, {
        user_id: data.user_id,
        email: data.email,
        role: data.role,
      })
    },
  })
}

export function useRegister() {
  return useMutation({
    mutationFn: (data: RegisterRequest) => api.register(data),
  })
}

export function useLogout() {
  const clearAuth = useAuthStore((s) => s.clearAuth)
  const queryClient = useQueryClient()
  // Returns a callback (not a mutation) because logout is synchronous and local-only
  return function logout() {
    queryClient.clear()
    clearAuth()
  }
}
