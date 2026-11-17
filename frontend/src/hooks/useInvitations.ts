import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../services/api'
import { useAuthStore } from '../store/useAuthStore'

const mineKey = ['invitations', 'mine'] as const
const companyKey = (companyId: number | null) => ['invitations', 'company', companyId] as const

/** The signed-in person's pending invitations. */
export function useMyInvitations(enabled = true) {
  return useQuery({ queryKey: mineKey, queryFn: api.getMyInvitations, enabled })
}

type Response =
  | { kind: 'accept'; invitationId: number }
  | { kind: 'decline'; invitationId: number }
  | { kind: 'token'; token: string }

/**
 * Accept or decline an invitation. An acceptance makes the person a member,
 * so the profile (their memberships) is read again.
 */
export function useRespondToInvitation() {
  const client = useQueryClient()
  const loadMe = useAuthStore(state => state.loadMe)
  return useMutation({
    mutationFn: (response: Response) => {
      switch (response.kind) {
        case 'accept': return api.acceptInvitation(response.invitationId)
        case 'decline': return api.declineInvitation(response.invitationId)
        case 'token': return api.acceptInvitationToken(response.token)
      }
    },
    onSuccess: async outcome => {
      await client.invalidateQueries({ queryKey: mineKey })
      if (outcome.status === 'accepted') await loadMe().catch(() => {})
    },
  })
}

/** The company's pending invitations, for its Managers and Owners. */
export function useCompanyInvitations(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: companyKey(companyId),
    queryFn: () => api.getCompanyInvitations(companyId as number),
    enabled: enabled && companyId !== null,
  })
}

/** Invite an address; refreshes the company's pending list. */
export function useInvite(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.inviteMember,
    onSuccess: () => client.invalidateQueries({ queryKey: companyKey(companyId) }),
  })
}

export function useRevokeInvitation(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (invitationId: number) => api.revokeInvitation(companyId as number, invitationId),
    onSuccess: () => client.invalidateQueries({ queryKey: companyKey(companyId) }),
  })
}
