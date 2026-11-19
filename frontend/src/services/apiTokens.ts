/**
 * A company's API tokens (`routers/api_tokens.py`, `services/platform/api_tokens.py`):
 * credentials a script or a CI job sends as `Authorization: Bearer`. Every
 * member reads the list; a Manager or an Owner creates and revokes one. A
 * token's role is never above its creator's and a token never creates another
 * (the backend refuses both). The token itself is returned once, by the
 * create call, and never again: the browser keeps it only in memory until the
 * person dismisses it.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'

/** The roles a token may carry: never Owner. */
export type TokenRole = 'Viewer' | 'Manager'

export interface ApiToken {
  id: number
  name: string
  scopes: string[]
  role: TokenRole
  /** The creator's address, as the backend recorded it. */
  created_by: string | null
  created_at: string | null
  expires_at: string | null
  expired: boolean
  revoked_at: string | null
  last_used_at: string | null
}

/** The create call's answer: the stored token plus the secret, shown once. */
export interface CreatedApiToken extends ApiToken {
  token: string
}

export interface NewApiToken {
  name: string
  role: TokenRole
  /** 1 to 365. The UI never sends `null` (no expiry): every token it makes expires. */
  expires_in_days: number
}

export type TokenState = 'active' | 'expired' | 'revoked'

/** Revoked wins over expired: it was a person's decision. */
export function tokenState(token: ApiToken): TokenState {
  if (token.revoked_at) return 'revoked'
  if (token.expired) return 'expired'
  return 'active'
}

export const apiTokensApi = {
  list: (companyId: number) => api.get<{ tokens: ApiToken[] }>('/v1/auth/tokens', { company_id: companyId }),
  create: (companyId: number, data: NewApiToken) =>
    api.post<CreatedApiToken>('/v1/auth/tokens', { company_id: companyId, ...data }),
  revoke: (companyId: number, tokenId: number) =>
    api.post<{ revoked: boolean }>(`/v1/auth/tokens/${tokenId}/revoke?company_id=${companyId}`),
}

const tokensKey = (companyId: number | null) => ['api-tokens', companyId] as const

export function useApiTokens(companyId: number | null) {
  return useQuery({
    queryKey: tokensKey(companyId),
    queryFn: () => apiTokensApi.list(companyId as number),
    enabled: companyId !== null,
  })
}

export function useCreateApiToken(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (data: NewApiToken) => apiTokensApi.create(companyId as number, data),
    onSuccess: () => client.invalidateQueries({ queryKey: tokensKey(companyId) }),
  })
}

export function useRevokeApiToken(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (tokenId: number) => apiTokensApi.revoke(companyId as number, tokenId),
    onSuccess: () => client.invalidateQueries({ queryKey: tokensKey(companyId) }),
  })
}
