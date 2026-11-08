/**
 * What a person's move on the board does (`routers/board_moves.py`,
 * `services/board_moves.py`).
 *
 * Moving a card to To do (`PATCH /v1/backlog/{id}`) starts it: the answer says
 * whether it was queued, whether its sprint was reopened, and why not when a
 * planned or paused sprint keeps it waiting. "Move to sprint…" carries a
 * blocked (or To do) task into another open sprint of the company, or a new one.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type BacklogItem } from './api'
import { QUERY_KEYS } from '../hooks/useQueries'

/** The board's answer to a status change; the extra fields only for a move to To do. */
export type BoardMoveResult = BacklogItem & {
  queued?: boolean
  reopened?: boolean
  sprint_status?: string
  reason?: string | null
}

export interface CarryTarget {
  id: number
  sprint_name: string
  status: string
}

export interface CarryResult {
  original_id: number
  backlog_item_id: number
  sprint_id: number
  sprint_name: string
  sprint_status: string
  created_sprint: boolean
  queued: boolean
  reason: string | null
}

export const boardMovesApi = {
  carryTargets: (itemId: number) =>
    api.get<{ sprints: CarryTarget[] }>(`/v1/backlog/${itemId}/carry_targets`),
  carryOver: (itemId: number, targetSprintId: number | null) =>
    api.post<CarryResult>(`/v1/backlog/${itemId}/carry_over`, { target_sprint_id: targetSprintId }),
}

/** The open sprints a task may move to, fetched only while the menu is open. */
export function useCarryTargets(itemId: number, enabled: boolean) {
  return useQuery({
    queryKey: ['backlog', itemId, 'carry-targets'] as const,
    queryFn: () => boardMovesApi.carryTargets(itemId),
    enabled,
  })
}

export function useCarryOver(sprintId: number, companyId: number | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ itemId, targetSprintId }: { itemId: number; targetSprintId: number | null }) =>
      boardMovesApi.carryOver(itemId, targetSprintId),
    onSuccess: result => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.sprintBoard(sprintId) })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.sprintBoard(result.sprint_id) })
      queryClient.invalidateQueries({ queryKey: ['inbox'] })
      if (companyId !== null) queryClient.invalidateQueries({ queryKey: QUERY_KEYS.sprints(companyId) })
    },
  })
}
