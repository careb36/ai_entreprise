import { useMutation } from '@tanstack/react-query'
import { api } from '../services/api'

/** The company's configuration as a file (issue #774): the Owner's, audited by the backend. */
export function useCompanyExport(companyId: number | null) {
  return useMutation({
    mutationFn: () => api.exportCompany(companyId as number),
  })
}
