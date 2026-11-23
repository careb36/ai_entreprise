import { useMutation } from '@tanstack/react-query'
import { api } from '../services/api'

/** A new company from an export file (issue #774), owned by whoever imports it. */
export function useCompanyImport() {
  return useMutation({
    mutationFn: ({ file, companyName }: { file: Record<string, unknown>; companyName?: string }) =>
      api.importCompany(file, companyName),
  })
}
