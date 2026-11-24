import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../services/api'

const actionsKey = (companyId: number | null) => ['owner-actions', companyId] as const
const runKey = (companyId: number | null, name: string, runId: number | null) =>
  ['owner-action-run', companyId, name, runId] as const

// While a run is running its page reads it again on this interval.
export const RUN_POLL_MS = 3000

/** The catalogue and each action's last runs (the company's Owner only). */
export function useOwnerActions(companyId: number | null) {
  return useQuery({
    queryKey: actionsKey(companyId),
    queryFn: () => api.getOwnerActions(companyId as number),
    enabled: companyId !== null,
    refetchInterval: query =>
      (query.state.data?.actions ?? []).some(a => a.running_run_id !== null) ? RUN_POLL_MS : false,
  })
}

/** One run, read again while it runs. */
export function useOwnerActionRun(companyId: number | null, name: string, runId: number | null) {
  return useQuery({
    queryKey: runKey(companyId, name, runId),
    queryFn: () => api.getOwnerActionRun(companyId as number, name, runId as number),
    enabled: companyId !== null && runId !== null,
    refetchInterval: query => (query.state.data?.status === 'running' ? RUN_POLL_MS : false),
  })
}

/** Preview (writes nothing), start (the preview's digest echoed) and cancel. */
export function useOwnerActionMutations(companyId: number | null) {
  const client = useQueryClient()
  const refresh = () => client.invalidateQueries({ queryKey: actionsKey(companyId) })
  return {
    preview: useMutation({
      mutationFn: ({ name, params }: { name: string; params: Record<string, unknown> }) =>
        api.previewOwnerAction(companyId as number, name, params),
    }),
    start: useMutation({
      mutationFn: ({ name, params, confirmation }: { name: string; params: Record<string, unknown>; confirmation: string }) =>
        api.startOwnerAction(companyId as number, name, params, confirmation),
      onSuccess: refresh,
    }),
    cancel: useMutation({
      mutationFn: ({ name, runId }: { name: string; runId: number }) =>
        api.cancelOwnerActionRun(companyId as number, name, runId),
      onSuccess: refresh,
    }),
  }
}
