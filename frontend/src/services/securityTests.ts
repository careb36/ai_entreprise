/**
 * The project's OWASP WSTG plan and the people's reviews of its tests
 * (`routers/security_tests.py`, issue #539). Every member reads the plan and
 * the reviews; a Manager records a person's review of one test, and the
 * backend computes the record's digest (never taken from the request). A
 * test's status comes only from a Verifier's observation or such a review.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'

export type WstgStatus = 'observed-pass' | 'observed-fail' | 'not-run' | 'needs-manual' | 'not-applicable'
export const WSTG_STATUSES: WstgStatus[] = ['observed-fail', 'needs-manual', 'not-run', 'observed-pass', 'not-applicable']

/** What set a row's status: a verification the Verifier observed, or a person's review. */
export interface WstgEvidence {
  kind: 'verification' | 'review'
  status: WstgStatus
  verification_id?: number | null
  run_id?: number | null
  observation?: string | null
  observed_at?: string | null
  review_id?: number
  reviewed_on?: string
  artifact?: string
  note?: string | null
  reviewer_role?: string
  record_sha256?: string
}

export interface WstgRow {
  id: string
  short: string
  name: string
  category: string
  applicable: boolean
  /** `auto` (a probe observes it), `manual` (a person reviews it), or none when not applicable. */
  mode: 'auto' | 'manual' | null
  /** A test a model proposed: a claim, never a rule. */
  claim?: boolean
  reason: string
  status: WstgStatus
  evidence: WstgEvidence | null
}

export interface WstgPlan {
  company_id: number
  catalogue: { commit: string; licence: string; attribution: string }
  signals: unknown
  counts: Record<WstgStatus, number>
  rows: WstgRow[]
}

export type WstgResult = 'pass' | 'fail' | 'not_applicable'

export interface WstgReview {
  id: number
  wstg_id: string
  result: WstgResult
  reviewer_name: string
  reviewer_role: string
  reviewed_on: string
  artifact: string
  note: string | null
  record_sha256: string
  created_at: string | null
}

export interface NewWstgReview {
  wstg_id: string
  result: WstgResult
  reviewer_name: string
  reviewed_on: string
  artifact: string
  note: string | null
}

const base = (companyId: number) => `/v1/companies/${companyId}/security/wstg`

export const securityTestsApi = {
  plan: (companyId: number) => api.get<WstgPlan>(base(companyId)),
  reviews: (companyId: number) => api.get<{ reviews: WstgReview[] }>(`${base(companyId)}/reviews`),
  record: (companyId: number, review: NewWstgReview) => api.post<WstgReview>(`${base(companyId)}/reviews`, review),
}

const planKey = (companyId: number | null) => ['wstg-plan', companyId] as const
const reviewsKey = (companyId: number | null) => ['wstg-reviews', companyId] as const

export function useWstgPlan(companyId: number | null) {
  return useQuery({ queryKey: planKey(companyId), queryFn: () => securityTestsApi.plan(companyId as number), enabled: companyId !== null })
}

export function useWstgReviews(companyId: number | null) {
  return useQuery({ queryKey: reviewsKey(companyId), queryFn: () => securityTestsApi.reviews(companyId as number), enabled: companyId !== null })
}

export function useRecordWstgReview(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (review: NewWstgReview) => securityTestsApi.record(companyId as number, review),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: planKey(companyId) })
      client.invalidateQueries({ queryKey: reviewsKey(companyId) })
    },
  })
}
