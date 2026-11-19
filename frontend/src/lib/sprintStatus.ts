/**
 * The status a sprint is shown with (#699, split from #555; F8 of the
 * self-hosting retrospective). A sprint reads `completed` once its tasks
 * settle, before its integration has merged their branches and run the suite;
 * the backend's `integration_state` (derived from the integration it recorded,
 * never an agent's report) says where that stands. A completed sprint whose
 * integration is still running, or failed, is not shown as completed.
 */
import type { IntegrationState } from '../services/api/sprints'

export function shownSprintStatus(status: string, integrationState?: IntegrationState | null): string {
  if (status !== 'completed') return status
  if (integrationState === 'integrating') return 'integrating'
  if (integrationState === 'failed') return 'integration_failed'
  return status
}
