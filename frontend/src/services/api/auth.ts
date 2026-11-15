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

export interface LoginResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
}

export interface RegisterResponse {
  user_id: number
  email: string
}

export const authEndpoints = () => ({
  login: (email: string, password: string) =>
    fetchApi<LoginResponse>('/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  register: (data: { email: string; full_name: string; password: string }) =>
    fetchApi<RegisterResponse>('/v1/auth/register', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  me: () => fetchApi<AuthProfile>('/v1/auth/me', { method: 'GET' }),
})
