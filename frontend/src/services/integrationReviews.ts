/**
 * A person's review of a sprint's integration, as on a run
 * (`routers/integration_reviews.py`, `services/release/integration_review.py`):
 * the diff the workspace daemon reads (`GET …/integration/diff`), the line
 * comments anchored to it and the submitted reviews. Viewers read them;
 * Managers write them; an Owner decides whether the company merges an
 * integration only once a person approved it.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchApi } from './api'
import type { CommentSide, ReviewEvent } from './runReviews'

export type IntegrationReviewOutcome = 'recorded' | 'approved' | 'changes_requested'

/** One file of the integration's diff, with its sha256 on each side, as the daemon read them. */
export interface IntegrationDiffFile {
  path: string
  old_path: string | null
  change: string
  additions: number
  deletions: number
  patch: string | null
  omitted: string | null
  sha256_old: string | null
  sha256_new: string | null
}

export interface IntegrationDiff {
  integration_id: number
  commit: string
  merge_base: string
  base: string
  base_commit: string
  files: IntegrationDiffFile[]
  truncated: boolean
  /** Computed by the backend from the daemon's per-file hashes. */
  diff_sha256: string
}

export interface IntegrationReview {
  id: number
  integration_id: number
  event: ReviewEvent
  body: string | null
  outcome: IntegrationReviewOutcome
  commit: string
  diff_sha256: string
  files: number
  published_url: string | null
  sod: string | null
  author_name: string
  created_at: string
}

export interface IntegrationComment {
  id: number
  integration_id: number
  path: string | null
  line: number | null
  side: CommentSide
  body: string
  commit: string
  file_sha256: string | null
  author_name: string
  review_id: number | null
  created_at: string
  resolved_at: string | null
}

/** What the integration's view shows of its reviews (also `review` in `GET …/integration`). */
export interface IntegrationReviewState {
  integration_id: number | null
  required: boolean
  approved: boolean
  approving_review_id: number | null
  latest: Pick<IntegrationReview, 'id' | 'event' | 'outcome' | 'author_name' | 'commit' | 'created_at'> | null
  open_comments: number
  published_url: string | null
}

export interface IntegrationReviews extends IntegrationReviewState {
  reviews: IntegrationReview[]
  comments: IntegrationComment[]
}

export interface NewIntegrationComment {
  integration_id: number
  path: string | null
  line: number | null
  side: CommentSide
  body: string
}

const base = (sprintId: number) => `/v1/sprints/${sprintId}/integration`

export const integrationReviewsApi = {
  /** Read by the daemon on the person's machine: git diff and one object read, sent once. */
  diff: (sprintId: number) => fetchApi<IntegrationDiff>(`${base(sprintId)}/diff`, { once: true, timeoutMs: 90_000 }),
  reviews: (sprintId: number) => api.get<IntegrationReviews>(`${base(sprintId)}/reviews`),
  addComment: (sprintId: number, comment: NewIntegrationComment) =>
    fetchApi<IntegrationComment>(`${base(sprintId)}/comments`, { method: 'POST', body: JSON.stringify(comment), once: true, timeoutMs: 90_000 }),
  resolveComment: (sprintId: number, commentId: number) =>
    api.post<IntegrationComment>(`${base(sprintId)}/comments/${commentId}/resolve`, {}),
  submit: (sprintId: number, integrationId: number, event: ReviewEvent, body: string) =>
    fetchApi<IntegrationReview>(`${base(sprintId)}/reviews`, {
      method: 'POST', body: JSON.stringify({ integration_id: integrationId, event, body: body || null }), once: true, timeoutMs: 90_000,
    }),
  setting: (companyId: number) => api.get<{ company_id: number; required: boolean }>(`/v1/companies/${companyId}/integration_review`),
  setRequired: (companyId: number, required: boolean) =>
    api.put<{ company_id: number; required: boolean }>(`/v1/companies/${companyId}/integration_review`, { required }),
}

export const integrationReviewsKey = (sprintId: number) => ['sprint-integration', sprintId, 'reviews'] as const
const diffKey = (sprintId: number) => ['sprint-integration', sprintId, 'diff'] as const

export function useIntegrationReviews(sprintId: number) {
  return useQuery({ queryKey: integrationReviewsKey(sprintId), queryFn: () => integrationReviewsApi.reviews(sprintId) })
}

/** The diff is read on demand: each read asks the daemon. */
export function useIntegrationDiff(sprintId: number, enabled: boolean) {
  return useQuery({ queryKey: diffKey(sprintId), queryFn: () => integrationReviewsApi.diff(sprintId), enabled, retry: false })
}

function useInvalidate(sprintId: number) {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ['sprint-integration', sprintId] })
}

export function useAddIntegrationComment(sprintId: number) {
  const invalidate = useInvalidate(sprintId)
  return useMutation({
    mutationFn: (comment: NewIntegrationComment) => integrationReviewsApi.addComment(sprintId, comment),
    onSuccess: invalidate,
  })
}

export function useResolveIntegrationComment(sprintId: number) {
  const invalidate = useInvalidate(sprintId)
  return useMutation({ mutationFn: (commentId: number) => integrationReviewsApi.resolveComment(sprintId, commentId), onSuccess: invalidate })
}

export function useSubmitIntegrationReview(sprintId: number) {
  const invalidate = useInvalidate(sprintId)
  return useMutation({
    mutationFn: ({ integrationId, event, body }: { integrationId: number; event: ReviewEvent; body: string }) =>
      integrationReviewsApi.submit(sprintId, integrationId, event, body),
    onSuccess: invalidate,
  })
}

export function useIntegrationReviewSetting(companyId: number) {
  return useQuery({ queryKey: ['integration-review-setting', companyId], queryFn: () => integrationReviewsApi.setting(companyId) })
}

export function useSetIntegrationReviewRequired(companyId: number, sprintId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (required: boolean) => integrationReviewsApi.setRequired(companyId, required),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['integration-review-setting', companyId] })
      queryClient.invalidateQueries({ queryKey: ['sprint-integration', sprintId] })
    },
  })
}
