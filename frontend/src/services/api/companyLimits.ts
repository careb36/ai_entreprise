import type { Http } from './client'

/** The limits a company may set (issue #766); each is optional. */
export interface CompanyLimits {
  tool_rounds?: number
  result_max_chars?: number
  max_output_tokens?: number
  max_reworks?: number
  sprint_budget_cents?: number
  budget_warn_percent?: number
}

export type CompanyLimitKey = keyof CompanyLimits

/** What `GET /v1/companies/{id}/limits` returns: the company's own, the deployment's defaults and the range. */
export interface CompanyLimitsState {
  company_id: number
  limits: CompanyLimits | null
  defaults: Required<CompanyLimits>
  floors: Required<CompanyLimits>
  ceilings: Required<CompanyLimits>
}

// Any member reads the limits; only an Owner changes them, audited.
export const companyLimitsEndpoints = (http: Http) => ({
  getCompanyLimits: (companyId: number) => http.get<CompanyLimitsState>(`/v1/companies/${companyId}/limits`),

  setCompanyLimits: (companyId: number, limits: CompanyLimits | null) =>
    http.put<CompanyLimitsState>(`/v1/companies/${companyId}/limits`, { limits }),
})
