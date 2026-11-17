import type { Http } from './client'
import type { CompanyRole } from './companies'

/**
 * A pending invitation to a company. Inviting adds nobody: the invitee
 * accepts or declines once signed in with the invited address.
 */
export interface Invitation {
  id: number
  company_id: number
  /** Present on the invitee's own list. */
  company_name?: string
  email: string
  human_role: CompanyRole
  invited_by: string | null
  status: 'pending' | 'accepted' | 'declined' | 'revoked'
  created_at: string
  expires_at: string
}

/**
 * The same answer whether or not the address has an account. `token` is
 * shown once, for a link the inviter may send; it accepts only for the
 * invited address.
 */
export interface InviteResponse {
  status: 'pending'
  detail: string
  token: string
}

export interface InvitationOutcome {
  status: 'accepted' | 'declined'
  company_id: number
  human_role: CompanyRole | null
}

export const invitationEndpoints = (http: Http) => ({
  // A Manager invites Viewers and Managers; only an Owner invites an Owner.
  inviteMember: (data: { company_id: number; email: string; human_role: CompanyRole }) =>
    http.post<InviteResponse>('/v1/company/invite', data),

  getCompanyInvitations: (companyId: number) =>
    http.get<{ invitations: Invitation[] }>(`/v1/companies/${companyId}/invitations`),

  revokeInvitation: (companyId: number, invitationId: number) =>
    http.delete<void>(`/v1/companies/${companyId}/invitations/${invitationId}`),

  /** The signed-in person's pending invitations. */
  getMyInvitations: () => http.get<{ invitations: Invitation[] }>('/v1/invitations'),

  acceptInvitation: (invitationId: number) =>
    http.post<InvitationOutcome>(`/v1/invitations/${invitationId}/accept`, {}),

  declineInvitation: (invitationId: number) =>
    http.post<InvitationOutcome>(`/v1/invitations/${invitationId}/decline`, {}),

  acceptInvitationToken: (token: string) =>
    http.post<InvitationOutcome>('/v1/invitations/accept', { token }),
})
