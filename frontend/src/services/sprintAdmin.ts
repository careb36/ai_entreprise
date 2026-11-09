/**
 * Editing, deleting and archiving a sprint (`routers/sprint_crud.py`).
 *
 * A Manager edits the name, goal and duration. An Owner deletes a halted
 * sprint: one with recorded work is archived instead, its evidence kept, and
 * an Owner can unarchive it.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type SprintSummary } from './api'
import { QUERY_KEYS } from '../hooks/useQueries'
import type { CostSummary } from './cost'

/** A sprint in the list, with what the archive and the edit form read. */
export type ManagedSprint = SprintSummary & {
  duration_days?: number | null
  archived_at?: string | null
  archived_by?: string | null
  /** Tokens and US cents (`services/cost.ts`). */
  cost?: CostSummary
}

export interface SprintEdit {
  sprint_name?: string
  goal?: string | null
  duration_days?: number | null
}

export type SprintRemoval =
  | { deleted: true; sprint_id: number }
  | { archived: true; sprint_id: number; archived_at: string; archived_by: string; evidence: string[] }

/** Statuses an Owner may delete or archive; a running sprint is stopped first. */
export const REMOVABLE_STATUSES = ['planned', 'cancelled', 'completed']

export const sprintAdminApi = {
  listWithArchived: (companyId: number) =>
    api.get<ManagedSprint[]>('/v1/sprints', { company_id: companyId, include_archived: 'true' }),
  update: (sprintId: number, edit: SprintEdit) =>
    api.patch<ManagedSprint & { changed: Record<string, unknown> }>(`/v1/sprints/${sprintId}`, edit),
  remove: (sprintId: number) => api.delete<SprintRemoval>(`/v1/sprints/${sprintId}`),
  unarchive: (sprintId: number) =>
    api.post<{ archived: false; sprint_id: number; status: string }>(`/v1/sprints/${sprintId}/unarchive`, {}),
}

const allKey = (companyId: number) => ['sprints', companyId, 'with-archived'] as const

/** The company's sprints archived ones included, only while asked for. */
export function useSprintsWithArchived(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: allKey(companyId ?? 0),
    queryFn: () => sprintAdminApi.listWithArchived(companyId as number),
    enabled: enabled && companyId !== null,
  })
}

function useSprintAdmin<T, V>(companyId: number, fn: (vars: V) => Promise<T>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      // Both lists start with ['sprints', companyId]; the inbox follows an archive.
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.sprints(companyId) })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.inbox(companyId) })
    },
  })
}

export const useUpdateSprint = (companyId: number) =>
  useSprintAdmin(companyId, ({ sprintId, edit }: { sprintId: number; edit: SprintEdit }) => sprintAdminApi.update(sprintId, edit))
export const useRemoveSprint = (companyId: number) =>
  useSprintAdmin(companyId, (sprintId: number) => sprintAdminApi.remove(sprintId))
export const useUnarchiveSprint = (companyId: number) =>
  useSprintAdmin(companyId, (sprintId: number) => sprintAdminApi.unarchive(sprintId))
