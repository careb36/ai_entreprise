/**
 * Who decides when a sprint's agents work: start a planned sprint, stop one
 * for good, and the company's autonomy level. Pause, resume and dispatch stay
 * on `api` (`pauseSprint`, `resumeSprint`, `dispatchSprint`).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type SprintBoard } from './api'
import { QUERY_KEYS } from '../hooks/useQueries'

/** A sprint as the board returns it, with why the worker paused it. */
export type SprintState = SprintBoard['sprint'] & { pause_reason?: string | null }

export const workControlApi = {
  startSprint: (sprintId: number) =>
    api.post<{ status: string; dispatched: number }>(`/v1/sprints/${sprintId}/start`, {}),
  stopSprint: (sprintId: number) =>
    api.post<{ status: string; stopped_tasks: number }>(`/v1/sprints/${sprintId}/stop`, {}),
  getAutonomy: (companyId: number) =>
    api.get<{ company_id: number; autonomy_level: number }>(`/v1/companies/${companyId}/autonomy`),
  setAutonomy: (companyId: number, autonomyLevel: number) =>
    api.put<{ company_id: number; autonomy_level: number }>(`/v1/companies/${companyId}/autonomy`, {
      autonomy_level: autonomyLevel,
    }),
  getGit: (companyId: number, check = false) =>
    api.get<GitWorkflow>(`/v1/companies/${companyId}/git`, check ? { check: 'true' } : undefined),
  setGit: (companyId: number, git: GitWorkflowChoice) => api.put<GitWorkflow>(`/v1/companies/${companyId}/git`, git),
}

/** What a Manager sets: the workflow and the branch task branches start from. */
export interface GitWorkflowChoice {
  git_workflow: boolean
  git_base_branch: string | null
}

/**
 * How the company's folder and base branch stand, as the daemon observed them
 * just now (`GET /v1/companies/{id}/git?check=true`), or why that is unknown.
 */
export interface GitBaseStatus {
  state: 'observed' | 'unknown'
  reason?: string
  path?: string
  is_repo?: boolean
  base_ref?: string
  base_commit?: string
  base_remote?: string | null
  base_remote_commit?: string
  base_behind_remote?: number
  base_ahead_of_remote?: number
  remote_unreachable?: string
}

/** Each task's deliveries as commits on its own branch, never in the person's checkout. */
export interface GitWorkflow extends GitWorkflowChoice {
  company_id: number
  base_status?: GitBaseStatus
}

const autonomyKey = (companyId: number) => ['company', companyId, 'autonomy'] as const

function useSprintMutation(fn: (sprintId: number) => Promise<unknown>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (_data, sprintId) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.sprintBoard(sprintId) })
    },
  })
}

export const useStartSprint = () => useSprintMutation(id => workControlApi.startSprint(id))
export const useStopSprint = () => useSprintMutation(id => workControlApi.stopSprint(id))

export function useAutonomy(companyId: number | null | undefined) {
  return useQuery({
    queryKey: autonomyKey(companyId ?? 0),
    queryFn: () => workControlApi.getAutonomy(companyId as number),
    enabled: Boolean(companyId),
  })
}

export function useSetAutonomy(companyId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (level: number) => workControlApi.setAutonomy(companyId, level),
    onSuccess: data => queryClient.setQueryData(autonomyKey(companyId), data),
  })
}

const gitKey = (companyId: number, check = false) => ['company', companyId, 'git', check ? 'checked' : 'plain'] as const

/** The company's Git workflow; with `check`, also how its folder and base stand (asks the daemon). */
export function useGitWorkflow(companyId: number | null | undefined, check = false) {
  return useQuery({
    queryKey: gitKey(companyId ?? 0, check),
    queryFn: () => workControlApi.getGit(companyId as number, check),
    enabled: Boolean(companyId),
  })
}

export function useSetGitWorkflow(companyId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (git: GitWorkflowChoice) => workControlApi.setGit(companyId, git),
    onSuccess: data => {
      queryClient.setQueryData(gitKey(companyId), data)
      // The checked view follows the saved choice; its observation holds only for the base it observed,
      // so another base drops it, and the daemon is asked again either way.
      queryClient.setQueryData<GitWorkflow>(gitKey(companyId, true), old => old
        ? { ...old, ...data, base_status: old.git_base_branch === data.git_base_branch ? old.base_status : undefined }
        : old)
      queryClient.invalidateQueries({ queryKey: gitKey(companyId, true) })
      queryClient.invalidateQueries({ queryKey: ['console-producers', companyId] })
    },
  })
}
