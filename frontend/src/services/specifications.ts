/**
 * Specifications: what the agents build from. The PO drafts one from a brief
 * (or a person writes it), a Manager answers its open questions and approves
 * it once the deterministic check finds nothing blocking, and a sprint planned
 * from it says which task covers each acceptance criterion. Viewers read;
 * Managers write.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Source } from '../components/SourcesConsulted'
import { ApiError, api, fetchApi } from './api'

export type SpecificationStatus = 'draft' | 'approved' | 'superseded'
export type FindingSeverity = 'blocking' | 'warning'
export type CoverageStatus = 'uncovered' | 'planned' | 'blocked' | 'done'

export interface Criterion {
  id: string
  given: string
  when: string
  then: string
}

export interface Requirement {
  id: string
  title: string
  description: string
  criteria: Criterion[]
}

export interface QualityAttribute {
  id: string
  attribute: string
  /** Carries a number: the check refuses a target nobody could measure. */
  target: string
}

export interface Question {
  question: string
  answer: string | null
}

export interface Finding {
  severity: FindingSeverity
  /** `REQ-2.1`, `NFR-1`, `Q1` or `spec`. */
  where: string
  code: string
  message: string
}

export interface Check {
  ok: boolean
  blocking: number
  warnings: number
  findings: Finding[]
}

export interface CoverageTask {
  id: number
  sprint_id: number
  title: string
  status: string
}

/** What the criterion's acceptance test said in the latest observed run of an implementation task. */
export type TestResult = 'pass' | 'fail' | 'missing'

export interface CoverageCriterion {
  id: string
  requirement_id: string
  tasks: CoverageTask[]
  /** QA's test-writing tasks of the criterion's requirement. */
  tests?: CoverageTask[]
  status: CoverageStatus
  /** The name its acceptance test carries (`test_req_1_2_…`). */
  test?: string
  /** True only when its acceptance test passed in an observed run. */
  verified?: boolean
  last_result?: TestResult | null
}

export interface Coverage {
  criteria: CoverageCriterion[]
  total: number
  covered: number
  done: number
  /** Criteria whose acceptance test passed in an observed run. */
  verified?: number
}

export interface Specification {
  id: number
  company_id: number
  title: string
  summary: string
  status: SpecificationStatus
  version: number
  supersedes_id: number | null
  requirements: Requirement[]
  quality: QualityAttribute[]
  constraints: string[]
  questions: Question[]
  /** The sources the PO consulted while drafting (the research tools); observations, not its claims. */
  sources?: Source[]
  /** `PO`, or the email of the person who wrote it. */
  drafted_by: string | null
  approved_by: string | null
  approved_at: string | null
  created_at: string | null
  updated_at: string | null
  check: Check
  /** Only on a single specification and on write responses. */
  coverage?: Coverage
}

/** What a person writes; ids are assigned by the backend on save. */
export interface SpecificationBody {
  title: string
  summary: string
  requirements: { title: string; description: string; criteria: { given: string; when: string; then: string }[] }[]
  quality: { attribute: string; target: string }[]
  constraints: string[]
}

export interface DraftRequest {
  company_id: number
  brief: string
  /** Revise this draft from its answered questions instead of starting afresh. */
  specification_id?: number
}

/** A 409 that carries more than a message: the findings that block an approval, or the revision already in draft. */
export class SpecificationConflict extends ApiError {
  constructor(status: number, message: string, public findings: Finding[] = [], public specificationId: number | null = null) {
    super(status, message, message)
    this.name = 'SpecificationConflict'
  }
}

/** A decision POST through the shared client (its 401 handling included); a structured refusal becomes a `SpecificationConflict`. */
async function postDecision<T>(endpoint: string): Promise<T> {
  try {
    return await api.post<T>(endpoint, {})
  } catch (error) {
    if (error instanceof ApiError && error.data && typeof error.data === 'object') {
      const structured = error.data as { message?: string; findings?: Finding[]; specification_id?: number }
      throw new SpecificationConflict(
        error.status,
        structured.message ?? error.message,
        Array.isArray(structured.findings) ? structured.findings : [],
        typeof structured.specification_id === 'number' ? structured.specification_id : null,
      )
    }
    throw error
  }
}

export const specificationsApi = {
  list: (companyId: number) =>
    api.get<{ specifications: Specification[] }>('/v1/specifications', { company_id: companyId }),
  get: (id: number) => api.get<Specification>(`/v1/specifications/${id}`),
  create: (companyId: number, body: SpecificationBody) =>
    api.post<Specification>('/v1/specifications', { company_id: companyId, ...body }),
  update: (id: number, body: SpecificationBody) => api.put<Specification>(`/v1/specifications/${id}`, body),
  /** The PO drafts or revises: an LLM call, sent once and waited on. */
  draft: (request: DraftRequest) =>
    fetchApi<Specification>('/v1/specifications/draft', { method: 'POST', body: JSON.stringify(request), once: true, timeoutMs: 240_000 }),
  answer: (id: number, index: number, answer: string) =>
    api.post<Specification>(`/v1/specifications/${id}/questions/${index}`, { answer }),
  approve: (id: number) => postDecision<Specification>(`/v1/specifications/${id}/approve`),
  revise: (id: number) => postDecision<Specification>(`/v1/specifications/${id}/revise`),
}

export const specificationsKey = (companyId: number) => ['specifications', companyId] as const
export const specificationKey = (id: number) => ['specification', id] as const

export function useSpecifications(companyId: number | null) {
  return useQuery({
    queryKey: specificationsKey(companyId ?? 0),
    queryFn: () => specificationsApi.list(companyId!),
    enabled: companyId !== null,
  })
}

export function useSpecification(id: number | null) {
  return useQuery({
    queryKey: specificationKey(id ?? 0),
    queryFn: () => specificationsApi.get(id!),
    enabled: id !== null,
  })
}

/** Every write answers with the full specification (coverage included): keep it, and refresh the list. */
function useSettle() {
  const queryClient = useQueryClient()
  return (specification: Specification) => {
    queryClient.setQueryData(specificationKey(specification.id), specification)
    queryClient.invalidateQueries({ queryKey: specificationsKey(specification.company_id) })
  }
}

export function useDraftSpecification() {
  const settle = useSettle()
  return useMutation({ mutationFn: (request: DraftRequest) => specificationsApi.draft(request), onSuccess: settle })
}

export function useCreateSpecification() {
  const settle = useSettle()
  return useMutation({
    mutationFn: ({ company_id, ...body }: { company_id: number } & SpecificationBody) => specificationsApi.create(company_id, body),
    onSuccess: settle,
  })
}

export function useUpdateSpecification() {
  const settle = useSettle()
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: SpecificationBody }) => specificationsApi.update(id, body),
    onSuccess: settle,
  })
}

export function useAnswerQuestion() {
  const settle = useSettle()
  return useMutation({
    mutationFn: ({ id, index, answer }: { id: number; index: number; answer: string }) => specificationsApi.answer(id, index, answer),
    onSuccess: settle,
  })
}

export function useApproveSpecification() {
  const settle = useSettle()
  return useMutation({ mutationFn: (id: number) => specificationsApi.approve(id), onSuccess: settle })
}

/** A new draft (version + 1) that supersedes the approved one once approved itself. */
export function useReviseSpecification() {
  const settle = useSettle()
  return useMutation({ mutationFn: (id: number) => specificationsApi.revise(id), onSuccess: settle })
}
