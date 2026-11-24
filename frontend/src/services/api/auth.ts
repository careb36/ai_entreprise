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
  /** The company's name, sent with the membership; absent from an older backend. */
  company_name?: string | null
  human_role: 'Owner' | 'Manager' | 'Viewer'
  /** The role the routes check: the own role raised by the member's teams (never to Owner). */
  effective_role?: 'Owner' | 'Manager' | 'Viewer' | null
}

export interface AuthProfile extends AuthUser {
  memberships: CompanyMembership[]
  /** Whether the account has a password to change (an SSO-only one has none). */
  has_password?: boolean
  /** The fewest characters a new password may have (PASSWORD_MIN_LENGTH). */
  password_min_length?: number
}

/**
 * A changed password. Every session of the person ended; a browser session
 * gets a new one in the answer's cookies, and with it a new CSRF value and
 * lifetime (absent when the change came with a Bearer token).
 */
export interface PasswordChanged {
  status: 'changed'
  sessions_ended: number
  csrf_token?: string
  session_expires_in?: number
}

/**
 * A browser sign-in (password or SSO). The session itself is in HttpOnly
 * cookies the page cannot read; the body carries only lifetimes and the CSRF
 * value (issue #524, docs/archive/design/SESSIONS.md).
 */
export interface LoginResponse {
  token_type: 'cookie'
  /** Seconds the access cookie lives; renewed by refresh. */
  expires_in: number
  /** Seconds until the session ends however often it is refreshed. */
  session_expires_in: number
  csrf_token: string
}

/**
 * The same answer whether or not the email already had an account, so it
 * carries no user id (issue #541).
 */
export interface RegisterResponse {
  status: 'accepted'
  detail: string
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

  /** The signed-in person changes their password (docs/design/change-password.md). */
  changePassword: (currentPassword: string, newPassword: string) =>
    fetchApi<PasswordChanged>('/v1/auth/password', {
      method: 'POST',
      once: true,
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),
})
