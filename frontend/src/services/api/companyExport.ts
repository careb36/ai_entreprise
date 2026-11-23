import type { Http } from './client'

/**
 * A company's configuration as `GET /v1/companies/{id}/export` returns it
 * (issue #774): secrets left out and named in `required_secrets`, lines that
 * looked like secrets replaced and their places listed in `redacted`. The
 * manifest's `sha256` is the backend's digest of `content`.
 */
export interface CompanyExport {
  manifest: {
    format: string
    company_id: number
    exported_at: string
    sha256: string
    sha256_of: string
  }
  content: Record<string, unknown>
  required_secrets: Record<string, unknown>[]
  redacted: Record<string, unknown>[]
}

export const companyExportEndpoints = (api: Http) => ({
  exportCompany: (companyId: number) => api.get<CompanyExport>(`/v1/companies/${companyId}/export`),
})
