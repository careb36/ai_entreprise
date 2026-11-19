import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { QUERY_KEYS } from '../services/queryKeys'
import { api, type TaskRelationCreate } from '../services/api'

/** A task's relations to other tasks, each named from this task's side (issue #533). */
export function useTaskRelations(taskId: number) {
  return useQuery({
    queryKey: QUERY_KEYS.taskRelations(taskId),
    queryFn: () => api.getTaskRelations(taskId),
  })
}

/** The relations of a sprint's tasks and their numbers; read when asked (`enabled`). */
export function useSprintRelations(sprintId: number, enabled: boolean) {
  return useQuery({
    queryKey: QUERY_KEYS.sprintRelations(sprintId),
    queryFn: () => api.getSprintRelations(sprintId),
    enabled,
  })
}

/** A change to a relation; both of its tasks show it, so every task's list is read again. */
function useRelationChange<T>(change: (input: T) => Promise<unknown>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: change,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: QUERY_KEYS.allTaskRelations() }),
  })
}

export function useAddTaskRelation(taskId: number) {
  return useRelationChange((data: TaskRelationCreate) => api.addTaskRelation(taskId, data))
}

export function useConfirmTaskRelation() {
  return useRelationChange((id: number) => api.confirmTaskRelation(id))
}

export function useRejectTaskRelation() {
  return useRelationChange((id: number) => api.rejectTaskRelation(id))
}

export function useRemoveTaskRelation() {
  return useRelationChange((id: number) => api.removeTaskRelation(id))
}
