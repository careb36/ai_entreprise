import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type SsoConnectionInput } from '../services/api'

const ssoKey = (companyId: number | null) => ['sso', companyId] as const

/** The company's SSO connection, for its Owners. */
export function useSsoSettings(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ssoKey(companyId),
    queryFn: () => api.getSso(companyId as number),
    enabled: enabled && companyId !== null,
  })
}

export function useSaveSso(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (data: SsoConnectionInput) => api.saveSso(companyId as number, data),
    onSuccess: () => client.invalidateQueries({ queryKey: ssoKey(companyId) }),
  })
}

export function useTestSso(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.testSso(companyId as number),
    onSuccess: () => client.invalidateQueries({ queryKey: ssoKey(companyId) }),
  })
}

export function useDeleteSso(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.deleteSso(companyId as number),
    onSuccess: () => client.invalidateQueries({ queryKey: ssoKey(companyId) }),
  })
}
