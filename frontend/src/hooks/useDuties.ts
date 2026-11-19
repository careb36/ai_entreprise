import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type AccessDecision, type ForgePolicy, type SodPolicyInput } from '../services/api'

const policyKey = (companyId: number | null) => ['sod-policy', companyId] as const
const reportKey = (companyId: number | null) => ['sod-report', companyId] as const
const reviewKey = (companyId: number | null) => ['access-review', companyId] as const
const accountsKey = (companyId: number | null) => ['service-accounts', companyId] as const

/** The segregation-of-duties policy, for the company's Owners. */
export function useSodPolicy(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: policyKey(companyId),
    queryFn: () => api.getSodPolicy(companyId as number),
    enabled: enabled && companyId !== null,
  })
}

type PolicyChange =
  | { kind: 'update'; data: SodPolicyInput }
  | { kind: 'grant'; reason: string }
  | { kind: 'withdraw' }

/** One mutation for the policy and the single-person exception; each is audited. */
export function useSodPolicyChange(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (change: PolicyChange): Promise<unknown> => {
      const id = companyId as number
      switch (change.kind) {
        case 'update': return api.updateSodPolicy(id, change.data)
        case 'grant': return api.grantSodException(id, change.reason)
        case 'withdraw': return api.withdrawSodException(id)
      }
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: policyKey(companyId) })
      client.invalidateQueries({ queryKey: reviewKey(companyId) })
    },
  })
}

/** The matrix and every approval with its requester, computed from the records. */
export function useSodReport(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: reportKey(companyId),
    queryFn: () => api.getSodReport(companyId as number),
    enabled: enabled && companyId !== null,
  })
}

/** Remove a member at once (an Owner's, audited): refreshes the review's listing and the service accounts. */
export function useRemoveMember(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (userId: number) => api.removeMember(companyId as number, userId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: reviewKey(companyId) })
      client.invalidateQueries({ queryKey: accountsKey(companyId) })
    },
  })
}

/** The access review: its schedule, what to review, and the history. */
export function useAccessReview(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: reviewKey(companyId),
    queryFn: () => api.getAccessReview(companyId as number),
    enabled: enabled && companyId !== null,
  })
}

export function useCompleteAccessReview(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ decisions, note }: { decisions: AccessDecision[]; note: string | null }) =>
      api.completeAccessReview(companyId as number, decisions, note),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: reviewKey(companyId) })
      client.invalidateQueries({ queryKey: accountsKey(companyId) })
    },
  })
}

export function useServiceAccounts(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: accountsKey(companyId),
    queryFn: () => api.getServiceAccounts(companyId as number),
    enabled: enabled && companyId !== null,
  })
}

const protectedKey = (companyId: number | null) => ['protected-paths', companyId] as const

/** Whether the company hosts the factory, so its protected paths hold, and the list in force. */
export function useProtectedPaths(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: protectedKey(companyId),
    queryFn: () => api.getProtectedPaths(companyId as number),
    enabled: enabled && companyId !== null,
  })
}

/** Turn the protected paths on or off: a signed-in Owner's, audited. */
export function useSetProtectedPaths(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (enabled: boolean) => api.setProtectedPaths(companyId as number, enabled),
    onSuccess: data => client.setQueryData(protectedKey(companyId), data),
  })
}

const forgeKey = (companyId: number | null) => ['forge-policy', companyId] as const

/** The company's forge policy (issue #727): any member reads it. */
export function useForgePolicy(companyId: number | null) {
  return useQuery({
    queryKey: forgeKey(companyId),
    queryFn: () => api.getForgePolicy(companyId as number),
    enabled: companyId !== null,
  })
}

/** Change the forge policy: an Owner's, audited. */
export function useSetForgePolicy(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (policy: ForgePolicy) => api.setForgePolicy(companyId as number, policy),
    onSuccess: data => client.setQueryData(forgeKey(companyId), data),
  })
}
