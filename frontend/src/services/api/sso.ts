import { API_BASE, fetchApi, type Http } from './client'
import type { LoginResponse } from './auth'

/** A company's identity provider, as the backend shows it: never the secret. */
export interface SsoConnection {
  id: number
  company_id: number
  slug: string
  preset: SsoPreset
  tenant: string | null
  issuer: string
  client_id: string
  has_client_secret: boolean
  allowed_domains: string[]
  default_role: 'Viewer' | 'Manager'
  auto_join: boolean
  enabled: boolean
  require_sso: boolean
  tested_at: string | null
  test_ok: boolean | null
  test_detail: string | null
  redirect_uri: string
  updated_at: string | null
}

export type SsoPreset = 'generic' | 'google' | 'entra'

export interface SsoSettings {
  connection: SsoConnection | null
  presets: SsoPreset[]
  redirect_uri: string
  owner_signed_in: boolean
}

export interface SsoConnectionInput {
  slug: string
  preset: SsoPreset
  tenant?: string | null
  issuer?: string | null
  client_id: string
  /** Omitted (undefined) keeps the stored secret; an empty string clears it. */
  client_secret?: string
  allowed_domains: string[]
  default_role: 'Viewer' | 'Manager'
  auto_join: boolean
  enabled: boolean
  require_sso: boolean
}

export interface SsoTestResult {
  ok: boolean
  detail: string
  observed: Record<string, unknown>
  connection: SsoConnection
}

/**
 * Where the browser goes to sign in with SSO: a full-page navigation, since
 * the backend sets a cookie and redirects to the identity provider.
 * `key` is the company's SSO name, an email or a domain.
 */
export function ssoStartUrl(key: string): string {
  return `${API_BASE}/v1/auth/sso/${encodeURIComponent(key.trim())}/start`
}

export const ssoEndpoints = (http: Http) => ({
  ssoExchange: (code: string) =>
    fetchApi<LoginResponse>('/v1/auth/sso/exchange', {
      method: 'POST',
      auth: false,
      once: true,
      body: JSON.stringify({ code }),
    }),

  getSso: (companyId: number) => http.get<SsoSettings>(`/v1/companies/${companyId}/sso`),

  saveSso: (companyId: number, data: SsoConnectionInput) =>
    http.put<SsoConnection>(`/v1/companies/${companyId}/sso`, data),

  testSso: (companyId: number) => http.post<SsoTestResult>(`/v1/companies/${companyId}/sso/test`),

  deleteSso: (companyId: number) => http.delete<void>(`/v1/companies/${companyId}/sso`),
})
