import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { QUERY_KEYS } from '../../services/queryKeys'
import {
  api,
  type TicketFilters,
  type TicketUpdateRequest,
} from '../../services/api'

// The help desk's tickets and the Support agent's record.
// Re-exported by hooks/useQueries.ts, which callers import.

export function useTickets(filters: TicketFilters) {
  return useQuery({
    queryKey: QUERY_KEYS.tickets(filters),
    queryFn: () => api.listTickets(filters),
  })
}

export function useTicket(ticketNum: string | null) {
  return useQuery({
    queryKey: QUERY_KEYS.ticket(ticketNum ?? ''),
    queryFn: () => api.getTicket(ticketNum!),
    enabled: !!ticketNum,
  })
}

export function useUpdateTicket() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ ticketNum, data }: { ticketNum: string; data: TicketUpdateRequest }) =>
      api.updateTicket(ticketNum, data),
    // Returned, so the caller's own onSuccess runs once the ticket is refetched.
    onSuccess: (_result, { ticketNum }) => Promise.all([
      queryClient.invalidateQueries({ queryKey: ['tickets'] }),
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.ticket(ticketNum) }),
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.supportAgentRecord() }),
    ]),
  })
}

/** Support admins only: how the Support agent's suggestions compared with their decisions. */
export function useSupportAgentRecord(enabled: boolean) {
  return useQuery({
    queryKey: QUERY_KEYS.supportAgentRecord(),
    queryFn: () => api.getSupportAgentRecord(),
    enabled,
  })
}
