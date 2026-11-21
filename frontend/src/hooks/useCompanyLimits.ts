import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type CompanyLimits } from '../services/api'

const limitsKey = (companyId: number | null) => ['company-limits', companyId] as const

/** The company's limits on its agents' work (issue #766): any member reads them. */
export function useCompanyLimits(companyId: number | null) {
  return useQuery({
    queryKey: limitsKey(companyId),
    queryFn: () => api.getCompanyLimits(companyId as number),
    enabled: companyId !== null,
  })
}

/** Change or clear the limits: an Owner's, audited. */
export function useSetCompanyLimits(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (limits: CompanyLimits | null) => api.setCompanyLimits(companyId as number, limits),
    onSuccess: data => client.setQueryData(limitsKey(companyId), data),
  })
}
