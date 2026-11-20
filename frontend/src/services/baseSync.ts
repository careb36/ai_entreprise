/**
 * The local copy of a company's repository against its remote (issue #745).
 *
 * - `checkFolder`: what the New company form shows before the company exists
 *   (`POST /v1/workspace/git_check`): the remote URL without credentials, the
 *   current and base branch, whether the checkout is clean. With
 *   `contactRemote` (the Owner pressed "Check remote"), the daemon also
 *   fetches the base's one branch and says how far it is behind or ahead.
 * - `syncCompany`: "Bring the local copy up to date" on the settings (Manager),
 *   a fast-forward held to the company's forge policy, recorded on the company.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type BaseSyncResult, type ForgePolicy } from './api'
import type { GitBaseStatus } from './workControl'

/**
 * The folder as the daemon observed it for the form, and the deployment's
 * ceiling: branch names, flags and counts only (no commit ids, no text from
 * the remote; `remote_unreachable` is a code).
 */
export interface FolderGitCheck extends Omit<GitBaseStatus, 'remote_unreachable' | 'base_commit' | 'base_remote_commit' | 'path'> {
  forge_policy_max?: ForgePolicy
  current_branch?: string | null
  clean?: boolean
  operation_in_progress?: string | null
  remote_url?: string | null
  remote_contacted?: boolean
  base_remote_not_contacted?: string
  base_not_on_remote?: string
  remote_unreachable?: 'remote_unreachable' | 'remote_needs_interaction'
  remote_needs_interaction?: boolean
}

export const baseSyncApi = {
  checkFolder: (projectPath: string, base: string | null, contactRemote: boolean) =>
    api.post<FolderGitCheck>('/v1/workspace/git_check', {
      project_path: projectPath,
      git_base_branch: base || null,
      contact_remote: contactRemote,
    }),
  syncCompany: (companyId: number) => api.post<BaseSyncResult>(`/v1/companies/${companyId}/git/sync_base`, {}),
}

const folderKey = (path: string, base: string | null) => ['folder-git', path, base ?? ''] as const

/** The folder's checkout and remote, read locally (nothing contacts the remote). */
export function useFolderGitCheck(path: string | null | undefined, base: string | null, enabled: boolean) {
  return useQuery({
    queryKey: folderKey(path ?? '', base),
    queryFn: () => baseSyncApi.checkFolder(path as string, base, false),
    enabled: enabled && Boolean(path),
    retry: false,
  })
}

/** "Check remote": the same, after a fetch of the base's one branch. */
export function useCheckRemote() {
  return useMutation({
    mutationFn: ({ path, base }: { path: string; base: string | null }) => baseSyncApi.checkFolder(path, base, true),
  })
}

/** "Bring the local copy up to date" on the company's settings; the folder is observed again after. */
export function useSyncBase(companyId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => baseSyncApi.syncCompany(companyId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['company', companyId, 'git'] }),
  })
}

/** The outcomes that moved nothing because nothing needed to move, or moved the base forward. */
export const SYNC_DONE = ['updated', 'up_to_date', 'ahead']
