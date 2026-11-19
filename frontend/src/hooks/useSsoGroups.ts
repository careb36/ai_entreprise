import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type GroupMappingInput } from '../services/api'

const groupsKey = (companyId: number | null) => ['sso-groups', companyId] as const

/** The SSO connection's group mapping, for its Owners (issue #703). */
export function useGroupMappings(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: groupsKey(companyId),
    queryFn: () => api.getGroupMappings(companyId as number),
    enabled: enabled && companyId !== null,
    retry: false,
  })
}

export type GroupDecision =
  | { kind: 'approve'; approvalId: number }
  | { kind: 'reject'; approvalId: number; reason: string | null }
  | { kind: 'withdraw'; approvalId: number }
  | { kind: 'confirmAlone'; approvalId: number; confirmation: string; reason: string }

/** Save the mapping; teams are read again, since a removed pair removes members. */
export function useSaveGroupMappings(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (data: GroupMappingInput) => api.saveGroupMappings(companyId as number, data),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: groupsKey(companyId) })
      client.invalidateQueries({ queryKey: ['teams', companyId] })
    },
  })
}

/** A decision on a pending Manager pair: approve, reject, withdraw or confirm alone. */
export function useGroupDecision(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (decision: GroupDecision) => {
      const id = companyId as number
      switch (decision.kind) {
        case 'approve': return api.approveManagerPair(id, decision.approvalId)
        case 'reject': return api.rejectManagerPair(id, decision.approvalId, decision.reason)
        case 'withdraw': return api.withdrawManagerPair(id, decision.approvalId)
        case 'confirmAlone':
          return api.confirmManagerPairAlone(id, decision.approvalId, decision.confirmation, decision.reason)
      }
    },
    onSuccess: () => client.invalidateQueries({ queryKey: groupsKey(companyId) }),
  })
}
