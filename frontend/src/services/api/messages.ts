import type { Http } from './client'

/** What a message does in the conversation between roles. */
export const MESSAGE_KINDS = ['question', 'answer', 'handoff', 'request', 'decision', 'escalation', 'retrospective', 'proposal'] as const
export type MessageKind = typeof MESSAGE_KINDS[number]

/** One message between the company's agents. A claim, never evidence. */
export interface AgentMessage {
  id: number
  sprint_id: number | null
  backlog_item_id: number | null
  task_title: string | null
  from_name: string
  from_role: string
  /** null: the whole team. */
  to_role: string | null
  kind: MessageKind
  body: string
  created_at: string | null
}

export interface MessageFilters {
  sprint_id?: number
  backlog_item_id?: number
  kind?: MessageKind
  limit?: number
}

export type ProcedureStatus = 'proposed' | 'active' | 'rejected' | 'retired'
/** The role name of procedures every role follows. */
export const ALL_ROLES = 'All'

/** The board's numbers before a procedure (baseline) or after it (outcome). */
export interface ProcedureMetrics {
  sprints: number
  tasks: number
  rework_per_task: number
  blocked_rate: number
  /** On outcomes: kept, reverted, unmeasured, superseded, restored or retired. */
  result?: string
  by?: number | string
}

export interface Procedure {
  id: number
  role: string
  title: string
  body: string
  status: ProcedureStatus
  version: number
  supersedes_id: number | null
  proposed_by: string
  rationale: string | null
  source_sprint_id: number | null
  baseline: ProcedureMetrics | null
  outcome: ProcedureMetrics | null
  decided_by: string | null
  created_at: string | null
  decided_at: string | null
  retired_at: string | null
}

/** A task blocked until a person acts: the worker will not move it on its own. */
export interface InboxBlockedTask {
  id: number
  title: string
  assignee: string | null
  sprint_id: number
  sprint_name: string
  sprint_status: string
  reason: string
  /** The ScrumMaster's answer to the escalation, when it gave one: a claim for the person to weigh. */
  triage: { decision: 'retry' | 'person'; why: string; brief: string } | null
  frozen: boolean
  /** Approved by QA but high risk: waits for a person to accept it (mark it done) or send it back. */
  risk_hold?: boolean
  updated_at: string | null
}

export interface InboxProposal {
  id: number
  role: string
  title: string
  proposed_by: string
  source_sprint_id: number | null
  created_at: string | null
}

/** Everything the company needs a person for. */
export interface Inbox {
  blocked_tasks: InboxBlockedTask[]
  proposals: InboxProposal[]
  pending_runs: number
  total: number
}

export const messageEndpoints = (api: Http) => ({
  getMessages: (companyId: number, filters: MessageFilters = {}) => {
    const params: Record<string, string | number> = { company_id: companyId }
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined) params[key] = value
    }
    return api.get<{ messages: AgentMessage[] }>('/v1/messages', params)
  },

  getInbox: (companyId: number) =>
    api.get<Inbox>('/v1/inbox', { company_id: companyId }),

  getProcedures: (companyId: number) =>
    api.get<{ procedures: Procedure[] }>('/v1/procedures', { company_id: companyId }),

  createProcedure: (data: { company_id: number; role: string; title: string; body: string }) =>
    api.post<Procedure>('/v1/procedures', data),

  decideProcedure: (procedureId: number, decision: 'adopt' | 'reject' | 'retire') =>
    api.post<Procedure>(`/v1/procedures/${procedureId}/${decision}`, {}),
})
