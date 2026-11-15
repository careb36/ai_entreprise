/**
 * Reviewing a run like a pull request: comments on the changed lines
 * (`/v1/agent_runs/{id}/comments`) and submitted reviews
 * (`/v1/agent_runs/{id}/reviews`). Viewers read them; Managers write them.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchApi } from './api'
import { QUERY_KEYS } from './queryKeys'
import { runChangesKey } from './reviewChanges'

export type CommentSide = 'new' | 'old'
export type ReviewEvent = 'comment' | 'approve' | 'request_changes'
export type ReviewOutcome = 'recorded' | 'sent_back' | 'accepted' | 'approval_recorded'

export interface ReviewComment {
  id: number
  agent_run_id: number
  path: string | null
  line: number | null
  side: CommentSide
  body: string
  author_name: string
  review_id: number | null
  created_at: string
  resolved_at: string | null
  /** Brought from the pull or merge request the run was published as. */
  source: 'github' | 'gitlab' | null
  external_id: string | null
  external_url: string | null
  /** A review submitted there: `CHANGES_REQUESTED`, `APPROVED`, `COMMENTED`. */
  external_state: string | null
}

export interface SyncResult {
  publication_id: number
  read: number
  added: number
  updated: number
  truncated: boolean
}

export interface RunReview {
  id: number
  agent_run_id: number
  event: ReviewEvent
  body: string | null
  outcome: ReviewOutcome
  author_name: string
  created_at: string
}

/** `local`: merged into a base branch of the local repository, nothing pushed. */
export type PublishHow = 'gh' | 'glab' | 'compare' | 'local'

/** A run's branch pushed to its platform, and the request opened for it (`how`). */
export interface RunPublication {
  id: number
  agent_run_id: number
  provider: string | null
  branch: string
  base: string
  commit: string
  url: string | null
  how: PublishHow
  /** The request was already open: its description was brought up to date. */
  updated: boolean
  published_by: string
  created_at: string
}

/** What undoing a delivery put back (`restored`) and removed (files the delivery created). */
export interface UndoResult {
  run_id: number
  snapshot: string
  restored: { path: string; sha256: string }[]
  removed: { path: string }[]
  undone_by: string
  audit_log_id: number | null
}

export interface NewComment {
  path: string | null
  line: number | null
  side: CommentSide
  body: string
}

export const reviewsApi = {
  comments: (companyId: number, runId: number) =>
    api.get<{ comments: ReviewComment[] }>(`/v1/agent_runs/${runId}/comments`, { company_id: companyId }),
  addComment: (companyId: number, runId: number, comment: NewComment) =>
    api.post<ReviewComment>(`/v1/agent_runs/${runId}/comments`, { company_id: companyId, ...comment }),
  resolveComment: (companyId: number, runId: number, commentId: number) =>
    api.post<ReviewComment>(`/v1/agent_runs/${runId}/comments/${commentId}/resolve?company_id=${companyId}`, {}),
  reviews: (companyId: number, runId: number) =>
    api.get<{ reviews: RunReview[] }>(`/v1/agent_runs/${runId}/reviews`, { company_id: companyId }),
  submit: (companyId: number, runId: number, event: ReviewEvent, body: string) =>
    api.post<RunReview>(`/v1/agent_runs/${runId}/reviews`, { company_id: companyId, event, body: body || null }),
  publications: (companyId: number, runId: number) =>
    api.get<{ publications: RunPublication[] }>(`/v1/agent_runs/${runId}/publications`, { company_id: companyId }),
  /** A push and the platform's CLI on the person's machine: sent once, never retried, waited on past the backend's 150 s. */
  publish: (companyId: number, runId: number) =>
    fetchApi<RunPublication>(`/v1/agent_runs/${runId}/publish?company_id=${companyId}`, { method: 'POST', body: '{}', once: true, timeoutMs: 180_000 }),
  /** Merges the task branch into the base branch on the daemon's machine; no remote involved. */
  merge: (companyId: number, runId: number) =>
    fetchApi<RunPublication>(`/v1/agent_runs/${runId}/merge?company_id=${companyId}`, { method: 'POST', body: '{}', once: true, timeoutMs: 180_000 }),
  /** Reads the request's comments on the platform through the daemon: a few paginated calls. */
  syncComments: (companyId: number, runId: number, publicationId: number) =>
    fetchApi<SyncResult>(`/v1/agent_runs/${runId}/publications/${publicationId}/sync?company_id=${companyId}`, { method: 'POST', body: '{}', once: true, timeoutMs: 240_000 }),
  /** Restores files on the person's machine: sent once, never retried. */
  undo: (companyId: number, runId: number) =>
    fetchApi<UndoResult>(`/v1/agent_runs/${runId}/undo?company_id=${companyId}`, { method: 'POST', body: '{}', once: true, timeoutMs: 90_000 }),
}

const commentsKey = (companyId: number, runId: number) => ['agent-run', companyId, runId, 'comments'] as const
const reviewsKey = (companyId: number, runId: number) => ['agent-run', companyId, runId, 'reviews'] as const
const publicationsKey = (companyId: number, runId: number) => ['agent-run', companyId, runId, 'publications'] as const

export function useReviewComments(companyId: number, runId: number) {
  return useQuery({ queryKey: commentsKey(companyId, runId), queryFn: () => reviewsApi.comments(companyId, runId) })
}

export function useRunReviews(companyId: number, runId: number) {
  return useQuery({ queryKey: reviewsKey(companyId, runId), queryFn: () => reviewsApi.reviews(companyId, runId) })
}

export function useAddReviewComment(companyId: number, runId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (comment: NewComment) => reviewsApi.addComment(companyId, runId, comment),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: commentsKey(companyId, runId) }),
  })
}

export function useResolveReviewComment(companyId: number, runId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (commentId: number) => reviewsApi.resolveComment(companyId, runId, commentId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: commentsKey(companyId, runId) }),
  })
}

/** A submitted review may send a task back or accept it: the run, the board and the inbox follow. */
export function useSubmitReview(companyId: number, runId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ event, body }: { event: ReviewEvent; body: string }) => reviewsApi.submit(companyId, runId, event, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.agentRun(companyId, runId) })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.inbox(companyId) })
      queryClient.invalidateQueries({ queryKey: ['sprint'] })
    },
  })
}

export function useRunPublications(companyId: number, runId: number) {
  return useQuery({ queryKey: publicationsKey(companyId, runId), queryFn: () => reviewsApi.publications(companyId, runId) })
}

/** Push the run's task branch and open its pull or merge request (Managers). */
export function usePublishRun(companyId: number, runId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => reviewsApi.publish(companyId, runId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: publicationsKey(companyId, runId) }),
  })
}

/** Merge the run's branch into the local repository's base branch (Managers). */
export function useMergeRun(companyId: number, runId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => reviewsApi.merge(companyId, runId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: publicationsKey(companyId, runId) }),
  })
}

/** Bring the comments people left on the pull or merge request to the run (Managers). */
export function useSyncComments(companyId: number, runId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (publicationId: number) => reviewsApi.syncComments(companyId, runId, publicationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: commentsKey(companyId, runId) }),
  })
}

/** Undo the run's delivery from the daemon's snapshot (Managers); the task's status stays. */
export function useUndoDelivery(companyId: number, runId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => reviewsApi.undo(companyId, runId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: runChangesKey(companyId, runId) }),
  })
}
