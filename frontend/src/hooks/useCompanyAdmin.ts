import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../services/api'

/** What deleting the company would remove (signed-in Owner); read only when asked. */
export function useCompanyDeletion(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ['company-deletion', companyId],
    queryFn: () => api.companyDeletion(companyId as number),
    enabled: companyId !== null && enabled,
  })
}

export function useRenameCompany(companyId: number | null) {
  return useMutation({
    mutationFn: (name: string) => api.renameCompany(companyId as number, name),
  })
}

export function useDeleteCompany(companyId: number | null) {
  return useMutation({
    mutationFn: (confirmName: string) => api.deleteCompany(companyId as number, confirmName),
  })
}
