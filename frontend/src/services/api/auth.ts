import { fetchApi } from './client'

export interface AuthUser {
  user_id: number
  email: string
  full_name: string
  /** Listed in the backend's SUPPORT_ADMIN_EMAILS: triages every ticket. */
  support_admin?: boolean
}

export interface CompanyMembership {
  company_id: number
  human_role: 'Owner' | 'Manager' | 'Viewer'
  /** The role the routes check: the own role raised by the member's teams (never to Owner). */
  effective_role?: 'Owner' | 'Manager' | 'Viewer' | null
}

export interface AuthProfile extends AuthUser {
  memberships: CompanyMembership[]
}

/**
 * A browser sign-in (password or SSO). The session itself is in HttpOnly
 * cookies the page cannot read; the body carries only lifetimes and the CSRF
 * value (issue #524, docs/design/SESSIONS.md).
 */
export interface LoginResponse {
  token_type: 'cookie'
  /** Seconds the access cookie lives; renewed by refresh. */
  expires_in: number
  /** Seconds until the session ends however often it is refreshed. */
  session_expires_in: number
  csrf_token: string
}

export interface RegisterResponse {
  user_id: number
  email: string
}

export const authEndpoints = () => ({
  login: (email: string, password: string) =>
    fetchApi<LoginResponse>('/v1/auth/login', {
      method: 'POST',
      auth: false,
      once: true,
      body: JSON.stringify({ email, password }),
    }),

  /** Ends the session on the server (revokes it) and clears its cookies. */
  logout: () =>
    fetchApi<void>('/v1/auth/logout', { method: 'POST', auth: false, once: true }),

  register: (data: { email: string; full_name: string; password: string }) =>
    fetchApi<RegisterResponse>('/v1/auth/register', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  me: () => fetchApi<AuthProfile>('/v1/auth/me', { method: 'GET' }),
})
