import type { Http } from './client'

/** A team's role scope inside its company: never Owner. */
export type TeamRole = 'Viewer' | 'Manager'

export type MembershipSource = 'owner' | 'sso'

export interface TeamExclusion {
  id: number
  user_id: number
  created_by: number
  created_at: string | null
  reason: string
}

export interface ManualException {
  id: number
  user_id: number
  created_by: number
  created_at: string | null
}

/** Who converting a team to SSO keeps, as manual exceptions; valid 15 minutes, once. */
export interface MembershipSourcePreview {
  id: number
  team_id: number
  members: number[]
  expires_at: string
  kept_as: 'manual_exception'
}

export interface Team {
  id: number
  company_id: number
  name: string
  description: string | null
  role: TeamRole
  /** "owner": members added by hand; "sso": from the identity provider's groups (issue #703). */
  membership_source?: MembershipSource
  /** The user ids of its members. */
  members: number[]
  /** Break-glass exclusions in force (SSO-managed teams). */
  exclusions?: TeamExclusion[]
  /** Members kept by hand when the team was converted to SSO. */
  manual_exceptions?: ManualException[]
  created_by: string | null
  created_at: string | null
  updated_at: string | null
}

/** A member of the company, with the role the routes check (raised by their teams). */
export interface TeamCandidate {
  user_id: number
  full_name: string
  email: string
  human_role: 'Owner' | 'Manager' | 'Viewer'
  effective_role: 'Owner' | 'Manager' | 'Viewer' | null
  team_ids: number[]
}

export interface TeamsResponse {
  company_id: number
  roles: TeamRole[]
  teams: Team[]
  members: TeamCandidate[]
}

export interface TeamInput {
  name: string
  description?: string | null
  role: TeamRole
}

/** Switching a team's membership source; to SSO, a team with members needs a confirmed preview. */
export interface MembershipSourceChange {
  membership_source: MembershipSource
  preview_id?: number | null
  confirm?: boolean
}

// Owner-only (a signed-in person, never an API token); every change is audited.
export const teamEndpoints = (http: Http) => ({
  getTeams: (companyId: number) => http.get<TeamsResponse>(`/v1/companies/${companyId}/teams`),

  createTeam: (companyId: number, data: TeamInput) =>
    http.post<Team>(`/v1/companies/${companyId}/teams`, data),

  updateTeam: (companyId: number, teamId: number, data: Partial<TeamInput> | MembershipSourceChange) =>
    http.patch<Team>(`/v1/companies/${companyId}/teams/${teamId}`, data),

  deleteTeam: (companyId: number, teamId: number) =>
    http.delete<void>(`/v1/companies/${companyId}/teams/${teamId}`),

  addTeamMember: (companyId: number, teamId: number, userId: number) =>
    http.put<Team>(`/v1/companies/${companyId}/teams/${teamId}/members/${userId}`, {}),

  removeTeamMember: (companyId: number, teamId: number, userId: number) =>
    http.delete<Team>(`/v1/companies/${companyId}/teams/${teamId}/members/${userId}`),

  previewMembershipSource: (companyId: number, teamId: number) =>
    http.post<MembershipSourcePreview>(`/v1/companies/${companyId}/teams/${teamId}/membership-source-preview`),

  // The break-glass exclusion: the other Owners are notified; a reason of 10 to 500 characters.
  createExclusion: (companyId: number, teamId: number, userId: number, reason: string) =>
    http.post<TeamExclusion>(`/v1/companies/${companyId}/teams/${teamId}/exclusions`, { user_id: userId, reason }),

  liftExclusion: (companyId: number, teamId: number, exclusionId: number, reason: string) =>
    http.delete<TeamExclusion>(`/v1/companies/${companyId}/teams/${teamId}/exclusions/${exclusionId}`, { reason }),
})
