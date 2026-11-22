import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../services/api'
import { QUERY_KEYS } from '../services/queryKeys'

const revisionsKey = (agentId: number) => ['agent-config-revisions', agentId] as const

/** Every change to an agent's configuration, newest first (issue #774): any member reads them. */
export function useAgentConfigRevisions(agentId: number, enabled = true) {
  return useQuery({
    queryKey: revisionsKey(agentId),
    queryFn: () => api.getAgentConfigRevisions(agentId),
    enabled,
  })
}

/** Put back what a revision replaced: a Manager's, recorded as a revision of its own. */
export function useRollbackAgentConfig(agentId: number, companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (revisionId: number) => api.rollbackAgentConfig(agentId, revisionId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: revisionsKey(agentId) })
      client.invalidateQueries({ queryKey: QUERY_KEYS.agents(companyId || 0) })
    },
  })
}
