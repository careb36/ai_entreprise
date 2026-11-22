import type { Http } from './client'
import type { ManagedAgent } from './agents'

/** One change to an agent's configuration (issue #774): an audit row in the company's chain. */
export interface AgentConfigRevision {
  revision_id: number
  at: string | null
  by: string | null
  user_id: number | null
  token_id: number | null
  source: 'edit' | 'rollback'
  reason: string | null
  rolled_back_from: number | null
  changed: string[]
  from: Record<string, unknown>
  to: Record<string, unknown>
}

export interface AgentRollbackResult {
  agent: ManagedAgent
  restored: string[]
  changed_since: string[]
}

// Viewers read the history; only a Manager rolls a revision back, as a revision of its own.
export const agentRevisionEndpoints = (http: Http) => ({
  getAgentConfigRevisions: (agentId: number) =>
    http.get<{ agent_id: number; revisions: AgentConfigRevision[] }>(`/v1/agents/${agentId}/config-revisions`),

  rollbackAgentConfig: (agentId: number, revisionId: number, reason?: string) =>
    http.post<AgentRollbackResult>(
      `/v1/agents/${agentId}/config-revisions/${revisionId}/rollback`,
      reason ? { reason } : {},
    ),
})
