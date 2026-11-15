import type { Http } from './client'

/** A team's role scope inside its company: never Owner. */
export type TeamRole = 'Viewer' | 'Manager'

export interface Team {
  id: number
  company_id: number
  name: string
  description: string | null
  role: TeamRole
  /** The user ids of its members. */
  members: number[]
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

// Owner-only (a signed-in person, never an API token); every change is audited.
export const teamEndpoints = (http: Http) => ({
  getTeams: (companyId: number) => http.get<TeamsResponse>(`/v1/companies/${companyId}/teams`),

  createTeam: (companyId: number, data: TeamInput) =>
    http.post<Team>(`/v1/companies/${companyId}/teams`, data),

  updateTeam: (companyId: number, teamId: number, data: Partial<TeamInput>) =>
    http.patch<Team>(`/v1/companies/${companyId}/teams/${teamId}`, data),

  deleteTeam: (companyId: number, teamId: number) =>
    http.delete<void>(`/v1/companies/${companyId}/teams/${teamId}`),

  addTeamMember: (companyId: number, teamId: number, userId: number) =>
    http.put<Team>(`/v1/companies/${companyId}/teams/${teamId}/members/${userId}`, {}),

  removeTeamMember: (companyId: number, teamId: number, userId: number) =>
    http.delete<Team>(`/v1/companies/${companyId}/teams/${teamId}/members/${userId}`),
})
