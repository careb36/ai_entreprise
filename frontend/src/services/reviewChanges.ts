/**
 * What a run changed, the way a code review shows it
 * (`GET /v1/agent_runs/{id}/changes`): the files the workspace daemon
 * observed when it wrote them, or a pull request's diff. The files the
 * producer only declared travel apart, in `declared_not_observed`.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from './api'

export type FileChangeKind = 'added' | 'modified' | 'deleted' | 'renamed' | 'unchanged' | 'not_written'
export type DiffOmitted = 'binary' | 'too_large' | 'reply_size' | 'not_recorded'

export interface FileChange {
  path: string
  old_path: string | null
  /** `null` when the delivery predates the daemon's diffs. */
  change: FileChangeKind | null
  /** Why a file was not written (`rejected`, `skipped_protected`, …). */
  status?: string
  additions: number
  deletions: number
  /** The hunks, from the first `@@` on. */
  patch: string | null
  omitted: DiffOmitted | null
}

export interface RunChanges {
  run_id: number
  /** `artifact`: a diff the producer sent, with no observation of a disk. */
  source: 'workspace' | 'pull_request' | 'artifact' | null
  observed_by: { verification_id: number; observed_at: string } | null
  /** With the Git workflow: the branch and commit the delivery landed on, as the daemon's git said. */
  git?: { branch: string; base: string; commit: string | null; parent: string; worktree: string; repository: string } | null
  reason: string | null
  files: FileChange[]
  files_not_shown: number
  totals: { files: number; additions: number; deletions: number }
  declared_not_observed: string[]
}

export const reviewApi = {
  changes: (companyId: number, runId: number) =>
    api.get<RunChanges>(`/v1/agent_runs/${runId}/changes`, { company_id: companyId }),
}

/** Under the run's own key, so verifying the run again refreshes its changes. */
export const runChangesKey = (companyId: number, runId: number) => ['agent-run', companyId, runId, 'changes'] as const

export function useRunChanges(companyId: number, runId: number) {
  return useQuery({
    queryKey: runChangesKey(companyId, runId),
    queryFn: () => reviewApi.changes(companyId, runId),
  })
}
