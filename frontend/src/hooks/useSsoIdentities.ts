import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../services/api'

const identitiesKey = ['sso', 'identities', 'mine'] as const

/** The signed-in person's own SSO identities and the companies they can link. */
export function useMySsoIdentities() {
  return useQuery({ queryKey: identitiesKey, queryFn: () => api.getMySsoIdentities() })
}

/** Begin a link: the backend answers the provider's URL, and the browser goes there. */
export function useStartSsoLink() {
  return useMutation({
    mutationFn: (companyId: number) => api.startSsoLink(companyId),
    onSuccess: ({ authorization_url }) => window.location.assign(authorization_url),
  })
}

export function useUnlinkSsoIdentity() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (identityId: number) => api.unlinkMySsoIdentity(identityId),
    onSuccess: () => client.invalidateQueries({ queryKey: identitiesKey }),
  })
}
