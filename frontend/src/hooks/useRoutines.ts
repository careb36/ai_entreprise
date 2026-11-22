import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type RoutineInput } from '../services/api'

const routinesKey = (companyId: number | null) => ['routines', companyId] as const
const revisionsKey = (routineId: number) => ['routine-revisions', routineId] as const

/** The company's routines (issue #774): any member reads them. */
export function useRoutines(companyId: number | null) {
  return useQuery({
    queryKey: routinesKey(companyId),
    queryFn: () => api.getRoutines(companyId as number),
    enabled: companyId !== null,
  })
}

/** Write, change, delete, run or restore a routine: a Manager's, each audited. */
export function useRoutineActions(companyId: number | null) {
  const client = useQueryClient()
  const refresh = (routineId?: number) => {
    client.invalidateQueries({ queryKey: routinesKey(companyId) })
    if (routineId !== undefined) client.invalidateQueries({ queryKey: revisionsKey(routineId) })
  }
  return {
    create: useMutation({
      mutationFn: (data: RoutineInput) => api.createRoutine(companyId as number, data),
      onSuccess: () => refresh(),
    }),
    update: useMutation({
      mutationFn: ({ routineId, data }: { routineId: number; data: Partial<RoutineInput> }) =>
        api.updateRoutine(routineId, data),
      onSuccess: (_, { routineId }) => refresh(routineId),
    }),
    remove: useMutation({
      mutationFn: (routineId: number) => api.deleteRoutine(routineId),
      onSuccess: () => refresh(),
    }),
    run: useMutation({
      mutationFn: ({ routineId, values }: { routineId: number; values?: Record<string, string> }) =>
        api.runRoutine(routineId, values),
      onSuccess: () => refresh(),
    }),
    restore: useMutation({
      mutationFn: ({ routineId, number }: { routineId: number; number: number }) =>
        api.restoreRoutineRevision(routineId, number),
      onSuccess: (_, { routineId }) => refresh(routineId),
    }),
  }
}

/** A routine's revisions, newest first. */
export function useRoutineRevisions(routineId: number, enabled = true) {
  return useQuery({
    queryKey: revisionsKey(routineId),
    queryFn: () => api.getRoutineRevisions(routineId),
    enabled,
  })
}
