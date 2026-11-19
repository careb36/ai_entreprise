/**
 * A task's run secrets (`routers/run_secrets.py`, issue #357): values one
 * task's run may read, stored encrypted by the backend. Every member reads
 * their names; a Manager or an Owner adds or deletes one. A value is sent
 * once and never returned: the list holds names only.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'

export interface RunSecret {
  id: number
  name: string
  task_id: number
}

/** The backend's rule for a name: an environment variable's. */
export const RUN_SECRET_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/

export const runSecretsApi = {
  list: (taskId: number) => api.get<{ secrets: RunSecret[] }>('/v1/run-secrets', { task_id: taskId }),
  create: (companyId: number, taskId: number, name: string, value: string) =>
    api.post<RunSecret>('/v1/run-secrets', { company_id: companyId, task_id: taskId, name, value }),
  remove: (companyId: number, secretId: number) =>
    api.delete<{ deleted: boolean }>(`/v1/run-secrets/${secretId}?company_id=${companyId}`),
}

const secretsKey = (taskId: number) => ['run-secrets', taskId] as const

export function useRunSecrets(taskId: number) {
  return useQuery({ queryKey: secretsKey(taskId), queryFn: () => runSecretsApi.list(taskId) })
}

export function useAddRunSecret(companyId: number, taskId: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ name, value }: { name: string; value: string }) => runSecretsApi.create(companyId, taskId, name, value),
    onSuccess: () => client.invalidateQueries({ queryKey: secretsKey(taskId) }),
  })
}

export function useDeleteRunSecret(companyId: number, taskId: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (secretId: number) => runSecretsApi.remove(companyId, secretId),
    onSuccess: () => client.invalidateQueries({ queryKey: secretsKey(taskId) }),
  })
}
