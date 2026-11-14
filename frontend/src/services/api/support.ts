import { fetchBlob, type Http } from './client'

// ── Help desk ───────────────────────────────────────────────────────────────

export interface SupportChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export type TicketSeverity = 'low' | 'medium' | 'high' | 'critical'
export type TicketCategory = 'bug' | 'feature' | 'question' | 'incident'
export type TicketStatus = 'open' | 'in_progress' | 'waiting' | 'resolved' | 'closed'

export interface BugReportRequest {
  title: string
  description: string
  steps?: string
  expected?: string
  actual?: string
  severity?: TicketSeverity
  reporter_email?: string
  browser_info?: Record<string, unknown>
}

export interface BugReportResponse {
  ticket_num: string
  status: string
  [key: string]: unknown
}

export interface TicketFilters {
  reporter_email?: string
  status?: TicketStatus
  severity?: TicketSeverity
  limit?: number
}

export interface SupportTicket {
  id: number
  ticket_num: string
  title: string
  /** What the reporter chose, or null when they chose none. */
  severity: string | null
  category: string | null
  status: string
  reporter_email: string | null
  assigned_to: string | null
  /** A support admin's decision, kept apart from the reporter's choice. */
  decided_severity?: string | null
  decided_category?: string | null
  created_at: string | null
  updated_at: string | null
  resolved_at: string | null
}

export interface TicketUpdateRequest {
  status: TicketStatus
  resolution?: string
  assigned_to?: string
  /** The support admin's decision, apart from the reporter's choice and the agent's suggestion. Omitted keeps the earlier one; '' clears it. */
  severity?: TicketSeverity | ''
  category?: TicketCategory | ''
}


/** Per field: tickets the Support agent triaged and a person then decided. */
export interface AgentRecordField {
  decided: number
  agent_agreed: number
  /** Decided tickets the agent gave no usable answer for (misses too). */
  agent_unusable: number
  /** Decided tickets whose reporter chose a value: the reporters' baseline. */
  reporter_chose: number
  reporter_agreed: number
}

export interface SupportAgentRecord {
  severity: AgentRecordField
  category: AgentRecordField
}

export interface SupportTicketDetail extends SupportTicket {
  description: string
  steps: string | null
  expected: string | null
  actual: string | null
  resolution: string | null
  /** The Support agent's triage: suggestions beside the reporter's choice. */
  agent_reply?: string | null
  suggested_severity?: string | null
  suggested_category?: string | null
  triaged_by?: string | null
  triaged_at?: string | null
  /** Who last changed the decision, and when. */
  decided_by?: string | null
  decided_at?: string | null
  [key: string]: unknown
}

export const supportEndpoints = (api: Http) => ({
  // Support
  // Help desk (`/v1/support/*`).
  supportChat: (messages: SupportChatMessage[]) =>
    api.post<{ reply?: string }>('/v1/support/chat', { messages }),

  reportBug: (data: BugReportRequest) =>
    api.post<BugReportResponse>('/v1/support/report_bug', data),

  listTickets: (filters: TicketFilters = {}) => {
    const params: Record<string, string | number> = {}
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== '') params[key] = value
    }
    return api.get<{ tickets: SupportTicket[]; count: number }>('/v1/support/tickets', params)
  },

  getTicket: (ticketNum: string) =>
    api.get<SupportTicketDetail>(`/v1/support/tickets/${encodeURIComponent(ticketNum)}`),

  updateTicket: (ticketNum: string, data: TicketUpdateRequest) =>
    api.patch<{ ticket_num: string; status: TicketStatus; updated_at: string }>(
      `/v1/support/tickets/${encodeURIComponent(ticketNum)}`,
      data,
    ),

  /** How the Support agent's suggestions compared with support admins' decisions. */
  getSupportAgentRecord: () => api.get<SupportAgentRecord>('/v1/support/agent_record'),

  /** The audit log as a JSONL file; 404 until a workflow has written one. */
  downloadSupportLogs: () => fetchBlob('/v1/support/logs'),
})
