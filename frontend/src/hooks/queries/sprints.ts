import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { useAppStore } from '../../store/useAppStore'
import { QUERY_KEYS } from '../../services/queryKeys'
import {
  api,
  ApiError,
  isClientError,
  type BacklogItem,
} from '../../services/api'

// Sprints and their backlog: planning, starting, the board, dispatch, pause and resume, and their analytics.
// Re-exported by hooks/useQueries.ts, which callers import.

export function useStartSprint() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.startSprint,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sprint'] })
      queryClient.invalidateQueries({ queryKey: ['sprints'] })
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

export function useSprints(companyId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.sprints(companyId ?? 0),
    queryFn: () => api.listSprints(companyId!),
    enabled: companyId !== null,
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
    if (query.error instanceof ApiError && [403, 404].includes(query.error.status)) {
      const state = useAppStore.getState()
      if (sprintId !== null && state.activeSprintId === sprintId) {
        state.setActiveSprint(null)
      }
    }
  }, [sprintId, query.error])

  return query
}

/**
 * Who works on the sprint now and its health tiles, from the backend's records
 * (the Live page). Polled every 5 s while the sprint runs, every 30 s otherwise.
 */
export function useSprintLiveState(sprintId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.sprintLiveState(sprintId || 0),
    queryFn: () => api.getSprintLiveState(sprintId!),
    enabled: !!sprintId,
    refetchInterval: query => isClientError(query.state.error) ? false : query.state.data?.running ? 5_000 : 30_000,
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
      queryClient.invalidateQueries({ queryKey: ['inbox'] })
    },
  })
}

export function useDispatchSprint() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (sprintId: number) => api.dispatchSprint(sprintId),
    onSuccess: (_data, sprintId) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.sprintBoard(sprintId) })
    },
  })
}

export function usePauseSprint() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (sprintId: number) => api.pauseSprint(sprintId),
    onSuccess: (_data, sprintId) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.sprintBoard(sprintId) })
    },
  })
}

export function useResumeSprint() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (sprintId: number) => api.resumeSprint(sprintId),
    onSuccess: (_data, sprintId) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.sprintBoard(sprintId) })
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
