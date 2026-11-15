/**
 * The intake queue (issue #532): requests that reach the company from a
 * ticket, the client's brief, an integration or chat, until a Manager accepts,
 * declines, marks a duplicate or snoozes them. The Support agent's suggestion
 * is a claim shown beside a person's decision, never merged with it. Viewers
 * read and send; Managers decide.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'

export const INTAKE_STATUSES = ['pending', 'accepted', 'declined', 'duplicate', 'snoozed'] as const
export type IntakeStatus = typeof INTAKE_STATUSES[number]
export const INTAKE_CATEGORIES = ['bug', 'feature', 'question', 'incident'] as const
export type IntakeSource = 'ticket' | 'brief' | 'webhook' | 'chat'

export interface IntakeLink {
  kind: 'intake' | 'task' | 'specification'
  id: number
}

export interface IntakeItem {
  id: number
  company_id: number
  source: IntakeSource
  source_ref: string | null
  submitted_by: string | null
  title: string
  body: string
  status: IntakeStatus
  snoozed_until: string | null
  duplicate_of: IntakeLink | null
  outcome: IntakeLink | null
  suggestion: {
    status: IntakeStatus | null
    category: string | null
    duplicate_of: string | null
    reason: string | null
    by: string | null
    at: string | null
  } | null
  decision: {
    status: IntakeStatus | null
    category: string | null
    reason: string | null
    by: string | null
    at: string | null
  } | null
  created_at: string | null
  updated_at: string | null
  history?: { action: string; actor: string | null; from: string | null; to: string | null; reason: string | null; at: string | null }[]
}

export interface IntakeList {
  items: IntakeItem[]
  pending: number
  statuses: IntakeStatus[]
  categories: string[]
}

export interface IntakeRecord {
  received: number
  by_status: Record<IntakeStatus, number>
  recorded: number
  recorded_share: number | null
  median_seconds_to_decision: number | null
  agreement: Record<string, { decided: number; agreed: number; unusable: number }>
  category_agreement: { compared: number; agreed: number }
}

interface DecisionFields { category?: string | null; reason?: string | null }

export type IntakeDecision =
  | ({ action: 'accept'; target: 'task' | 'specification'; sprint_id?: number; assignee?: string; priority?: string; specification_id?: number } & DecisionFields)
  | ({ action: 'decline'; reason: string } & DecisionFields)
  | ({ action: 'duplicate'; of_item_id?: number; of_task_id?: number } & DecisionFields)
  | ({ action: 'snooze'; until: string } & DecisionFields)
  | { action: 'reopen' }

export const intakeApi = {
  list: (companyId: number, status?: IntakeStatus) =>
    api.get<IntakeList>(`/v1/companies/${companyId}/intake`, status ? { status } : undefined),
  record: (companyId: number) => api.get<IntakeRecord>(`/v1/companies/${companyId}/intake-record`),
  send: (companyId: number, body: { title: string; body: string }) =>
    api.post<IntakeItem>(`/v1/companies/${companyId}/intake`, body),
  decide: (itemId: number, { action, ...body }: IntakeDecision) =>
    api.post<IntakeItem>(`/v1/intake/${itemId}/${action}`, action === 'reopen' ? undefined : body),
}

const intakeKey = (companyId: number, status?: IntakeStatus) => ['intake', companyId, status ?? 'all'] as const
const recordKey = (companyId: number) => ['intake', companyId, 'record'] as const

export function useIntake(companyId: number | null, status?: IntakeStatus) {
  return useQuery({
    queryKey: intakeKey(companyId ?? 0, status),
    queryFn: () => intakeApi.list(companyId as number, status),
    enabled: companyId !== null,
  })
}

export function useIntakeRecord(companyId: number | null) {
  return useQuery({
    queryKey: recordKey(companyId ?? 0),
    queryFn: () => intakeApi.record(companyId as number),
    enabled: companyId !== null,
  })
}

function useSettle(companyId: number) {
  const queryClient = useQueryClient()
  return () => {
    queryClient.invalidateQueries({ queryKey: ['intake', companyId] })
    // The inbox counts what waits in the queue.
    queryClient.invalidateQueries({ queryKey: ['inbox', companyId] })
  }
}

export function useSendIntake(companyId: number) {
  const settle = useSettle(companyId)
  return useMutation({ mutationFn: (body: { title: string; body: string }) => intakeApi.send(companyId, body), onSuccess: settle })
}

export function useDecideIntake(companyId: number) {
  const settle = useSettle(companyId)
  return useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: IntakeDecision }) => intakeApi.decide(id, decision),
    onSuccess: settle,
  })
}
