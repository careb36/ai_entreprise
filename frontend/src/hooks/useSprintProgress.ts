import { useQuery } from '@tanstack/react-query'
import { QUERY_KEYS } from '../services/queryKeys'
import { api } from '../services/api'

/** A sprint's progress (issue #531): live while it runs, its sealed snapshot once closed. */
export function useSprintProgress(sprintId: number, refreshMs: number | false = false) {
  return useQuery({
    queryKey: QUERY_KEYS.sprintProgress(sprintId),
    queryFn: () => api.getSprintProgress(sprintId),
    refetchInterval: refreshMs,
  })
}

/** Per requirement of a specification: claimed, done and verified across its sprints. */
export function useSpecificationProgress(specId: number | null | undefined) {
  return useQuery({
    queryKey: QUERY_KEYS.specificationProgress(specId ?? 0),
    queryFn: () => api.getSpecificationProgress(specId as number),
    enabled: typeof specId === 'number' && specId > 0,
  })
}
