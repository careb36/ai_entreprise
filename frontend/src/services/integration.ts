/**
 * A sprint's integration plan (`GET /v1/sprints/{id}/integration`, Viewer,
 * `services/integration.py`): the integration branch, the task branches in
 * the order they merge, and the dependencies that point outside the sprint.
 */
import { api } from './api'

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
}

export const integrationApi = {
  plan: (sprintId: number) => api.get<SprintIntegrationPlan>(`/v1/sprints/${sprintId}/integration`),
}
