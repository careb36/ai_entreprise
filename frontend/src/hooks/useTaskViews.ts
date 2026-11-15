import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { QUERY_KEYS } from '../services/queryKeys'
import { api, type SavedViewSettings, type TaskQuery } from '../services/api'

/** The tasks that pass a filter, evaluated on the server (issue #535). */
export function useFilteredTasks(query: TaskQuery | null) {
  const key = query ? JSON.stringify(query) : ''
  return useQuery({
    queryKey: QUERY_KEYS.filteredTasks(query?.companyId ?? 0, key),
    queryFn: () => api.filterTasks(query!),
    enabled: query !== null,
  })
}

/** The company's views and this person's private ones, with the allowlists. */
export function useSavedViews(companyId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.savedViews(companyId ?? 0),
    queryFn: () => api.getSavedViews(companyId!),
    enabled: companyId !== null,
  })
}

export function useSaveView(companyId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id?: number; data: Partial<SavedViewSettings> & { name: string } }) =>
      id ? api.updateSavedView(id, data) : api.createSavedView(companyId, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: QUERY_KEYS.savedViews(companyId) }),
  })
}

export function useDeleteView(companyId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.deleteSavedView(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: QUERY_KEYS.savedViews(companyId) }),
  })
}
