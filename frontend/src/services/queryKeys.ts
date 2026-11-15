/**
 * The React Query keys of the data read from the backend, in one place: the
 * hooks read with them and the service hooks invalidate them after a write.
 * `hooks/useQueries` re-exports them.
 */
import type { AgentRunFilters, MessageFilters, TicketFilters } from './api'

export const QUERY_KEYS = {
  capabilities: () => ['capabilities'] as const,
  workspaceStatus: () => ['workspace', 'status'] as const,
  sprints: (companyId: number) => ['sprints', companyId] as const,
  sprintBoard: (id: number) => ['sprint', id, 'board'] as const,
  quotas: (companyId: number) => ['enterprise', companyId, 'quotas'] as const,
  quotaHistory: (companyId: number, days: number) => ['enterprise', companyId, 'quota_history', days] as const,
  analytics: (sprintId: number) => ['analytics', sprintId] as const,
  orchestrationHealth: (companyId: number, weeks: number) => ['orchestration_health', companyId, weeks] as const,
  tickets: (filters: TicketFilters) => ['tickets', filters] as const,
  ticket: (num: string) => ['ticket', num] as const,
  supportAgentRecord: () => ['support-agent-record'] as const,
  reviewerRecord: (companyId: number) => ['reviewer-record', companyId] as const,
  lineage: (companyId: number | null, file: string) => ['lineage', companyId, file] as const,
  memory: (companyId: number | null, query: string) => ['memory', companyId, query] as const,
  models: () => ['models'] as const,
  agents: (companyId: number) => ['agents', companyId] as const,
  agentRuns: (companyId: number, filters: AgentRunFilters = {}) =>
    ['agent-runs', companyId, filters.producer ?? '', filters.status ?? '', filters.limit ?? 50,
      filters.external_ref ?? '', filters.sprint_id ?? ''] as const,
  agentRun: (companyId: number, runId: number) => ['agent-run', companyId, runId] as const,
  procedures: (companyId: number) => ['procedures', companyId] as const,
  inbox: (companyId: number) => ['inbox', companyId] as const,
  boardNotes: (sprintId: number) => ['sprint', sprintId, 'board-notes'] as const,
  allTaskRelations: () => ['task-relations'] as const,
  taskRelations: (taskId: number) => ['task-relations', taskId] as const,
  messages: (companyId: number, filters: MessageFilters = {}) =>
    ['messages', companyId, filters.sprint_id ?? '', filters.backlog_item_id ?? '', filters.kind ?? '', filters.limit ?? ''] as const,
  verifications: (companyId: number, runId: number) => ['agent-run', companyId, runId, 'verifications'] as const,
  approvals: (companyId: number, runId: number) => ['agent-run', companyId, runId, 'approvals'] as const,
  auditVerification: (companyId: number | null, externalRef: string) =>
    ['audit-verification', companyId, externalRef] as const,
}
