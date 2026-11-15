import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { QUERY_KEYS } from '../services/queryKeys'
import { api, type BoardNoteCreate } from '../services/api'

/** A sprint's board notes and the board's numbers; refreshed while the sprint runs. */
export function useBoardNotes(sprintId: number, refreshMs: number | false = false) {
  return useQuery({
    queryKey: QUERY_KEYS.boardNotes(sprintId),
    queryFn: () => api.getBoardNotes(sprintId),
    refetchInterval: refreshMs,
  })
}

export function useAddBoardNote(sprintId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: BoardNoteCreate) => api.addBoardNote(sprintId, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: QUERY_KEYS.boardNotes(sprintId) }),
  })
}

export function useMarkBoardNoteWrong(sprintId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) => api.markBoardNoteWrong(id, reason),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: QUERY_KEYS.boardNotes(sprintId) }),
  })
}
