import type { Http } from './client'

/** When a routine runs: every day or week, at HH:MM in an IANA timezone (issue #774). */
export interface RoutineSchedule {
  every: 'day' | 'week'
  at: string
  timezone: string
  weekday?: number
}

/** An input the routine's description uses as `{{name}}`. */
export interface RoutineVariable {
  name: string
  label?: string | null
  default?: string | null
  required: boolean
  options: string[]
}

export interface Routine {
  id: number
  company_id: number
  title: string
  description: string
  assignee: string
  schedule: RoutineSchedule
  enabled: boolean
  variables: RoutineVariable[]
  revision: number
  next_run_at: string | null
  last_run_at: string | null
  last_sprint_id: number | null
}

export interface RoutineInput {
  title: string
  description: string
  assignee: string
  schedule: RoutineSchedule
  enabled: boolean
  variables: RoutineVariable[]
}

export interface RoutineRevision {
  number: number
  snapshot: Omit<RoutineInput, never>
  change_summary: string | null
  restored_from: number | null
  created_by_user_id: number | null
  created_at: string | null
}

/** What a run did: the sprint it opened, or why it was skipped. */
export interface RoutineRunResult {
  sprint_id?: number
  skipped?: string
  open_sprint_id?: number
  revision?: number
  values?: Record<string, string>
}

// Any member reads routines; a Manager writes, runs and restores them, each audited.
export const routineEndpoints = (http: Http) => ({
  getRoutines: (companyId: number) =>
    http.get<{ company_id: number; routines: Routine[] }>(`/v1/companies/${companyId}/routines`),

  createRoutine: (companyId: number, data: RoutineInput) =>
    http.post<Routine>(`/v1/companies/${companyId}/routines`, data),

  updateRoutine: (routineId: number, data: Partial<RoutineInput>) =>
    http.patch<Routine>(`/v1/routines/${routineId}`, data),

  deleteRoutine: (routineId: number) => http.delete<{ deleted: number }>(`/v1/routines/${routineId}`),

  runRoutine: (routineId: number, values?: Record<string, string>) =>
    http.post<RoutineRunResult>(`/v1/routines/${routineId}/run`, values ? { values } : {}),

  getRoutineRevisions: (routineId: number) =>
    http.get<{ routine_id: number; revision: number; revisions: RoutineRevision[] }>(
      `/v1/routines/${routineId}/revisions`,
    ),

  restoreRoutineRevision: (routineId: number, number: number) =>
    http.post<Routine>(`/v1/routines/${routineId}/revisions/${number}/restore`),
})
