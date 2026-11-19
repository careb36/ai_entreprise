import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type RunWindow } from '../services/api'
import { QUERY_KEYS } from '../services/queryKeys'

const windowKey = (companyId: number | null) => ['run-window', companyId] as const

/** The company's run window (issue #736): any member reads it. */
export function useRunWindow(companyId: number | null) {
  return useQuery({
    queryKey: windowKey(companyId),
    queryFn: () => api.getRunWindow(companyId as number),
    enabled: companyId !== null,
  })
}

/** Change or turn off the run window: an Owner's, audited. */
export function useSetRunWindow(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (runWindow: RunWindow | null) => api.setRunWindow(companyId as number, runWindow),
    onSuccess: data => client.setQueryData(windowKey(companyId), data),
  })
}

/** Check and preview a window not saved yet; nothing is stored. */
export function usePreviewRunWindow(companyId: number | null) {
  return useMutation({
    mutationFn: (runWindow: RunWindow | null) => api.previewRunWindow(companyId as number, runWindow),
  })
}

/** Mark a sprint urgent (a Manager's, audited): its work ignores the window. */
export function useSetSprintUrgent(sprintId: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (urgent: boolean) => api.setSprintUrgent(sprintId, urgent),
    onSuccess: () => client.invalidateQueries({ queryKey: QUERY_KEYS.sprintBoard(sprintId) }),
  })
}
