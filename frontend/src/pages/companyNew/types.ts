import type { WorkspaceInspection } from '../../services/api'

// ── Types ────────────────────────────────────────────────────────────────────

export interface Agent {
  name: string
  role: string
  persona: string
  seniority: string
  specialty: string
  token_budget?: number
  reasoning?: string
  model?: string
  system_prompt?: string
}

export interface AgentDraft extends Agent {
  defaultSpecialtyKey?: 'productSpecialty' | 'qaSpecialty'
}

export interface ModelInfo {
  id: string
  provider: 'gemini' | 'deepseek'
  tier: 'strategic' | 'balanced' | 'code' | 'fast' | 'preview'
  description: string
}

export interface Permissions {
  git_access: string
  cloud_access: string
  db_access: string
  ci_access: string
  secrets_access: string
  require_approval_deploy: boolean
  require_approval_db_write: boolean
}

/** Result of asking the workspace daemon about a folder; `stale` when the field changed meanwhile. */
export type InspectOutcome =
  | { kind: 'ok'; result: WorkspaceInspection }
  | { kind: 'error' }
  | { kind: 'stale' }
