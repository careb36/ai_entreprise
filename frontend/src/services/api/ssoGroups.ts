import type { Http } from './client'
import type { TeamRole } from './teams'

/** The cap on what a group mapping may grant: never Owner (issue #703). */
export type GroupRoleCap = 'Viewer' | 'Manager'

export type ManagerApprovalState =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'withdrawn'
  | 'expired'
  | 'confirmed_single_owner'

export interface GroupPair {
  group_value: string
  team_id: number
}

export interface GroupMapping extends GroupPair {
  id: number
  /** Set for a pair to a Manager team: the proposal a second Owner approved. */
  approval_id: number | null
  created_by: number | null
  created_at: string | null
}

/** A pair to a Manager team, waiting for a second Owner (four-eyes). */
export interface ManagerApproval {
  id: number
  group_value: string
  team_id: number
  team_role: TeamRole
  group_role_cap: GroupRoleCap
  requested_by: number
  requested_at: string | null
  expires_at: string | null
  state: ManagerApprovalState
  decided_by: number | null
  decided_at: string | null
  reason: string | null
  reviewed_in: number | null
}

export interface GroupMappingView {
  connection_id: number
  preset: string
  groups_enabled: boolean
  groups_claim: string
  group_role_cap: GroupRoleCap
  /** `roles` (app roles) for Entra, `groups` otherwise. */
  proposed_claim: string
  /** Entra group ids are compared lower-cased. */
  compares_lower: boolean
  /** False for Google: its ID token carries no groups. */
  groups_available: boolean
  caps: GroupRoleCap[]
  max_pairs: number
  mappings: GroupMapping[]
  approvals: ManagerApproval[]
  sso_teams: { id: number; name: string; role: TeamRole }[]
  /** The last sign-in whose token left the groups out (Entra's overage). */
  last_overage: { observed_at: string | null; host: string | null } | null
}

export interface GroupMappingInput {
  groups_enabled: boolean
  groups_claim: string
  group_role_cap: GroupRoleCap
  mappings: GroupPair[]
}

// Owner-only (a signed-in person, never an API token); every change is audited.
export const ssoGroupEndpoints = (http: Http) => {
  const base = (companyId: number) => `/v1/companies/${companyId}/sso/group-mappings`
  return {
    getGroupMappings: (companyId: number) => http.get<GroupMappingView>(base(companyId)),

    saveGroupMappings: (companyId: number, data: GroupMappingInput) =>
      http.put<GroupMappingView>(base(companyId), data),

    approveManagerPair: (companyId: number, approvalId: number) =>
      http.post<GroupMappingView>(`${base(companyId)}/approvals/${approvalId}/approve`),

    rejectManagerPair: (companyId: number, approvalId: number, reason: string | null) =>
      http.post<GroupMappingView>(`${base(companyId)}/approvals/${approvalId}/reject`, { reason }),

    withdrawManagerPair: (companyId: number, approvalId: number) =>
      http.post<GroupMappingView>(`${base(companyId)}/approvals/${approvalId}/withdraw`),

    confirmManagerPairAlone: (companyId: number, approvalId: number, confirmation: string, reason: string) =>
      http.post<GroupMappingView>(`${base(companyId)}/approvals/${approvalId}/confirm-single-owner`, {
        confirmation,
        reason,
      }),
  }
}
