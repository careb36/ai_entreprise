import type { Http } from './client'

/**
 * The names a relation between two tasks reads under (issue #533): the three
 * stored kinds and `blocking`, the name of `blocked_by` from the other task.
 */
export const TASK_RELATION_NAMES = ['blocked_by', 'blocking', 'duplicate', 'relates_to'] as const
export type TaskRelationName = typeof TASK_RELATION_NAMES[number]
export type TaskRelationState = 'proposed' | 'confirmed'

/** The other task of a relation, as the backend reads it. */
export interface RelatedTask {
  id: number
  title: string | null
  status: string | null
  sprint_id: number | null
  sprint_name?: string | null
}

/**
 * A relation as one task's side reads it. A proposed one is its proposer's
 * claim; only a person or a deterministic rule confirms it.
 */
export interface TaskRelation {
  id: number
  relation: TaskRelationName
  stored_kind: 'blocked_by' | 'duplicate' | 'relates_to'
  task_id: number
  related_task_id: number
  other_task: RelatedTask
  state: TaskRelationState
  proposed_by: string
  /** `person`, `system` or the proposing agent's role. */
  proposed_role: string
  confirmed_by: string | null
  confirmed_at: string | null
  /** A confirmed blocked_by within one sprint: dispatch waits on it. */
  drives_dispatch: boolean
  created_at: string | null
}

export interface TaskRelationsResponse {
  task_id: number
  relations: TaskRelation[]
  names: TaskRelationName[]
}

export interface TaskRelationCreate {
  related_task_id: number
  relation: TaskRelationName
  confirm?: boolean
}

/** The relations of a sprint's tasks, each read from its sprint task's side, and their numbers. */
export interface SprintRelationsResponse {
  sprint_id: number
  relations: TaskRelation[]
  metrics: {
    total: number
    proposed: number
    confirmed: number
    by_kind: Record<'blocked_by' | 'duplicate' | 'relates_to', number>
    /** Confirmed duplicate pairs where both tasks were delivered: work run twice. */
    duplicates_delivered: number
  }
}

export const taskRelationEndpoints = (api: Http) => ({
  getSprintRelations: (sprintId: number) =>
    api.get<SprintRelationsResponse>(`/v1/sprints/${sprintId}/relations`),

  getTaskRelations: (taskId: number) =>
    api.get<TaskRelationsResponse>(`/v1/tasks/${taskId}/relations`),

  addTaskRelation: (taskId: number, data: TaskRelationCreate) =>
    api.post<TaskRelation>(`/v1/tasks/${taskId}/relations`, data),

  confirmTaskRelation: (relationId: number) =>
    api.post<TaskRelation>(`/v1/task_relations/${relationId}/confirm`),

  rejectTaskRelation: (relationId: number) =>
    api.post<{ removed: boolean }>(`/v1/task_relations/${relationId}/reject`, {}),

  removeTaskRelation: (relationId: number) =>
    api.post<{ removed: boolean }>(`/v1/task_relations/${relationId}/remove`, {}),
})
