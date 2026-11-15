/**
 * A sprint's integration plan (`GET /v1/sprints/{id}/integration`, Viewer,
 * `services/release/integration.py`): the integration branch, the task branches in
 * the order they merge, and the dependencies that point outside the sprint;
 * the latest integration and the state of its reviews.
 */
import { api } from './api'
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
  latest: { id: number; status: string; commit: string | null; branch: string; base: string } | null
  /** The latest integration's reviews (`services/release/integration_review.py`). */
  review?: IntegrationReviewState
}

export const integrationApi = {
  plan: (sprintId: number) => api.get<SprintIntegrationPlan>(`/v1/sprints/${sprintId}/integration`),
}
