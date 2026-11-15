import type { Http } from './client'
import type { WorkspaceInspection } from './workspace'

export interface CompanyInfo {
  id: number
  name: string
  autonomy_level: number
  token_quota: number
  token_used: number
}

export interface InitCompanyRequest {
  company_name: string
  description?: string
  default_scope_size?: 'S' | 'M' | 'L'
  autonomy_level?: number
  start_now?: boolean
  timezone?: string
  agents?: DiscoverTeamAgent[]
  permissions?: Record<string, string>
  governance?: Record<string, boolean>
  project_path?: string
  detected_stack?: DetectedStack
  /** Kept only when the daemon observes a repository at project_path; the base is where task branches start. */
  git_workflow?: boolean
  git_base_branch?: string | null
  /** The language of the project's documentation packs (issue #530). */
  doc_language?: 'en' | 'es'
}

export interface InitCompanyResponse {
  company_id: number
  sprint_id: number
  task_count: number
  message: string
}

export interface DetectedStack {
  name: string
  language: string
  all_languages?: Record<string, number>
  frameworks: string[]
  databases: string[]
  infra: string[]
  complexity: string
  file_count: number
  team_source?: 'ai' | 'rules'
}

export interface DiscoverTeamAgent {
  name?: string
  role: string
  seniority: string
  specialty: string
  persona?: string
  reasoning?: string
  model?: string
  system_prompt?: string
  token_budget?: number
}

export interface DiscoverTeamResponse {
  discovery_id: number | null
  suggested_team: DiscoverTeamAgent[]
  detected_stack: DetectedStack
  /** Canonical host path of the scanned folder; send this to init_company. */
  project_path?: string
  /** Same shape as GET /v1/workspace/inspect; null when scanned on the server. */
  project?: WorkspaceInspection | null
}

export type CompanyRole = 'Owner' | 'Manager' | 'Viewer'

export const companyEndpoints = (api: Http) => ({
  initCompany: (data: InitCompanyRequest) =>
    api.post<InitCompanyResponse>('/v1/init_company', data),

  discoverTeam: (companyId: number | null, projectPath: string) =>
    api.post<DiscoverTeamResponse>('/v1/discover_team', { company_id: companyId, project_path: projectPath }),

  // Company members. Owners invite users who already have an account.
  // 404 when the email has no account, 409 when it is already a member.
  inviteMember: (data: { company_id: number; email: string; human_role: CompanyRole }) =>
    api.post<{ status: string; message: string }>('/v1/company/invite', data),
})
