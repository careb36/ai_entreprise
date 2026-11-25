/**
 * A sprint's integration plan (`GET /v1/sprints/{id}/integration`, Viewer,
 * `services/release/integration.py`): the integration branch, the task branches in
 * the order they merge, and the dependencies that point outside the sprint;
 * the latest integration and the state of its reviews.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, fetchApi } from './api'
import type { IntegrationReviewState } from './integrationReviews'

export interface IntegrationTaskBranch {
  task_id: number
  title: string
  status: string
  branch: string
}

export interface UnmetDependency {
  task_id: number
  missing_dependency: number
}

export interface SprintIntegrationPlan {
  sprint_id: number
  sprint_name: string
  integration_branch: string
  task_branches: IntegrationTaskBranch[]
  merge_order: number[]
  unmet_dependencies: UnmetDependency[]
  /** The latest integration, as the daemon answered; none before the first. */
  latest: IntegrationRow | null
  /** The latest integration's reviews (`services/release/integration_review.py`). */
  review?: IntegrationReviewState
}

/** Where the latest integration was published (`integration_publish.record_publication`). */
export interface IntegrationPublication {
  provider: string | null
  branch: string
  base: string
  commit: string
  /** Only an https URL is kept by the backend. */
  url: string | null
  how: string
  updated: boolean
  published_by: string
}

/** The local merge of the latest integration (`local_merge.record_integration_merge`). */
export interface IntegrationLocalMerge {
  merged_by: string
  merged_at: string
  commit?: string
  base?: string
}

/** One integration as the backend stored it; the fields the page reads. */
export interface IntegrationRow {
  id: number
  status: string
  commit: string | null
  branch: string
  base: string
  publication?: IntegrationPublication | null
  /** ``auto_publish``: whether the sprint's draft request opened on its own after the close, and why not. */
  details?: { local_merge?: IntegrationLocalMerge; auto_publish?: { status: string; reason: string } } | null
}

/** A push and the daemon's suite take time: as long as the backend waits for the daemon, plus a margin. */
const ACTION_TIMEOUT_MS = 180_000

const action = <T,>(sprintId: number, path: string) =>
  fetchApi<T>(`/v1/sprints/${sprintId}/${path}`, { method: 'POST', body: '{}', once: true, timeoutMs: ACTION_TIMEOUT_MS })

export const integrationApi = {
  plan: (sprintId: number) => api.get<SprintIntegrationPlan>(`/v1/sprints/${sprintId}/integration`),
  /** Manager: merge the done task branches now and run the suite on the result. */
  integrate: (sprintId: number) => action<{ id: number; status: string }>(sprintId, 'integrate'),
  /** Manager, forge policy `publish`: push the integration branch and open the sprint's request. */
  publish: (sprintId: number) => action<IntegrationRow>(sprintId, 'integration/publish'),
  /** Manager, not the sprint's requester (segregation of duties): merge locally, push nothing. */
  merge: (sprintId: number) => action<IntegrationRow>(sprintId, 'integration/merge'),
}

export const integrationKey = (sprintId: number) => ['sprint-integration', sprintId] as const

/** One of the three actions; each refreshes the plan, the reviews and the diff (all under the plan's key). */
export function useIntegrationAction(sprintId: number, kind: 'integrate' | 'publish' | 'merge') {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => integrationApi[kind](sprintId) as Promise<unknown>,
    onSuccess: () => client.invalidateQueries({ queryKey: integrationKey(sprintId) }),
  })
}
