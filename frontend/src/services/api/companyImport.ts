import type { Http } from './client'

/** A value the file held that the import did not apply, and why. */
export interface ImportSkipped {
  what: string
  why: string
}

/**
 * Something the Owner sets up after the import: a provider connection to give
 * a key, or a secret the export left out (`kind` names which).
 */
export interface ImportToSetUp {
  provider_connection?: string
  provider?: string
  base_url?: string
  kind?: string
  connection?: string
  type?: string
  access?: string
  name?: string
  for?: string
}

/** What `POST /v1/companies/import` made from an export file (issue #774). */
export interface CompanyImportResult {
  company_id: number
  company_name: string
  created: { agents: number; business_units: number; routines: number }
  skipped: ImportSkipped[]
  to_set_up: ImportToSetUp[]
  routines_off: boolean
  /** The backend's own digest of the file's content: an observation. */
  sha256: string
  /** Whether the digest the file claims equals the backend's. */
  digest_matches: boolean
}

export const companyImportEndpoints = (api: Http) => ({
  importCompany: (file: Record<string, unknown>, companyName?: string) =>
    api.post<CompanyImportResult>('/v1/companies/import', {
      file,
      company_name: companyName?.trim() ? companyName.trim() : null,
    }),
})
