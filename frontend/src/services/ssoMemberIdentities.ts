/**
 * The members' single sign-on identities through the company's connection
 * (`routers/sso_link.py`, `services/platform/sso_link.py`): for a signed-in
 * Owner only, each member's linked identity with its address and when it was
 * linked and last used, and unlinking one. Unlinking a member's identity ends
 * the browser sessions that member started through single sign-on; the
 * backend audits it with the number of sessions ended.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'

export interface MemberSsoIdentity {
  id: number
  user_id: number
  /** The member's account address. */
  user_email: string
  /** The address the identity provider gave. */
  email: string | null
  linked_at: string | null
  last_login_at: string | null
}

export interface UnlinkResult {
  unlinked: boolean
  /** Whether the person still has another way to sign in (a password or another identity). */
  other_sign_in: boolean
  sessions_ended: number
}

const base = (companyId: number) => `/v1/companies/${companyId}/sso/identities`

export const memberSsoApi = {
  list: (companyId: number) => api.get<{ identities: MemberSsoIdentity[] }>(base(companyId)),
  unlink: (companyId: number, identityId: number) => api.delete<UnlinkResult>(`${base(companyId)}/${identityId}`),
}

const key = (companyId: number | null) => ['sso-member-identities', companyId] as const

export function useMemberSsoIdentities(companyId: number | null, enabled: boolean) {
  return useQuery({ queryKey: key(companyId), queryFn: () => memberSsoApi.list(companyId as number), enabled: enabled && companyId !== null })
}

export function useUnlinkMemberSso(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (identityId: number) => memberSsoApi.unlink(companyId as number, identityId),
    onSuccess: () => client.invalidateQueries({ queryKey: key(companyId) }),
  })
}
