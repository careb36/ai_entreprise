/**
 * The company's audit record as the backend verifies it, for every member
 * (Viewer): the whole audit chain walked end to end (`GET /v1/audit/chain`,
 * `audit_chain.verify_chain`), a task's event log with its own chain
 * (`GET /v1/tasks/{id}/events`), the head's statement a customer signs with
 * its own key and the signature it returns (`docs/archive/design/AUDIT_ANCHORS.md`,
 * customer_key), and an anchor's receipt to verify outside the factory.
 *
 * Every verdict here is the server's: the browser never recomputes a hash or
 * judges a signature (docs/POSITIONING.md, rule 6).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import type { AnchorCoverage } from './auditAnchors'

export type ChainStatus = 'empty' | 'intact' | 'broken'
export type ChainBreakReason = 'gap' | 'prev_mismatch' | 'hash_mismatch' | 'key_missing' | 'downgrade'

export interface ChainBreak {
  seq: number
  reason: ChainBreakReason | string
}

export interface ChainHead {
  seq: number
  chain_hash: string
}

export interface AuditChainReport {
  company_id: number
  status: ChainStatus
  /** Keyed (`hmac-sha256`), unkeyed (`unsigned`) or both (`mixed`); none when empty. */
  mode: 'hmac-sha256' | 'unsigned' | 'mixed' | null
  rows: number
  head: ChainHead | null
  /** Up to twenty problems, by sequence number. */
  breaks: ChainBreak[]
  anchors: AnchorCoverage
}

/** One event of a task, as `services/tasks/task_events.as_dict` returns it. */
export interface TaskEvent {
  id: number
  task_id: number
  sprint_id: number | null
  kind: string
  event_type: string
  actor: string | null
  run_id: number | null
  verification_id: number | null
  task_status: string | null
  legacy: boolean
  payload: Record<string, unknown>
  seq: number | null
  prev_hash: string | null
  chain_hash: string | null
  created_at: string | null
}

export interface TaskEventLog {
  task_id: number
  /** Which record the board reads for this task: its events, or the older comments. */
  source: 'events' | 'comments'
  chain: { task_id: number; status: ChainStatus; events: number; head?: ChainHead; breaks: ChainBreak[] }
  events: TaskEvent[]
}

/** The chain's head as the statement a customer key signs (pinned format). */
export interface HeadStatement {
  company_id: number
  seq: number
  chain_hash: string
  anchored_at: string
  statement: string
}

export interface AnchorExport {
  id: number
  kind: string
  seq: number
  chain_hash: string
  status: string
  statement: string
  token: string | null
  tsa_cert_pem: string | null
  signature: string | null
  public_key: string | null
  receipt_body: unknown
}

export const auditTrailApi = {
  chain: (companyId: number) => api.get<AuditChainReport>('/v1/audit/chain', { company_id: companyId }),
  taskEvents: (taskId: number) => api.get<TaskEventLog>(`/v1/tasks/${taskId}/events`),
  statement: (companyId: number) => api.get<HeadStatement>('/v1/audit/anchors/statement', { company_id: companyId }),
  sign: (companyId: number, head: HeadStatement, signature: string) =>
    api.post<{ id: number; kind: string; seq: number; status: string }>(
      `/v1/audit/anchors/signature?company_id=${companyId}`,
      { seq: head.seq, chain_hash: head.chain_hash, anchored_at: head.anchored_at, signature },
    ),
  exportAnchor: (companyId: number, anchorId: number) =>
    api.get<AnchorExport>(`/v1/audit/anchors/${anchorId}/export`, { company_id: companyId }),
}

/** The walk reads every row: run on demand, never on a timer. */
export function useVerifyChain(companyId: number | null) {
  return useMutation({ mutationFn: () => auditTrailApi.chain(companyId as number) })
}

export function useTaskEvents(taskId: number, enabled: boolean) {
  return useQuery({ queryKey: ['task-events', taskId], queryFn: () => auditTrailApi.taskEvents(taskId), enabled })
}

export function useHeadStatement(companyId: number | null) {
  return useMutation({ mutationFn: () => auditTrailApi.statement(companyId as number) })
}

export function useSubmitSignature(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ head, signature }: { head: HeadStatement; signature: string }) =>
      auditTrailApi.sign(companyId as number, head, signature),
    onSuccess: () => client.invalidateQueries({ queryKey: ['audit-anchors', companyId] }),
  })
}

export function useExportAnchor(companyId: number | null) {
  return useMutation({ mutationFn: (anchorId: number) => auditTrailApi.exportAnchor(companyId as number, anchorId) })
}

