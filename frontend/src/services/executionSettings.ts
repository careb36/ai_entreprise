/**
 * Where a company's agent-written code runs and what its research tools may
 * read (`routers/sprint_control.py`): the sandbox (host, container or bwrap,
 * with the environment variables a sandboxed run sees, by name only), the
 * reset of the project's dependency cache on the daemon's machine, and the
 * hosts the research tools may read beyond the deployment's defaults. Every
 * member reads them; a Manager changes them.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchApi } from './api'

export type SandboxMode = 'host' | 'container' | 'bwrap'
export const SANDBOX_MODES: SandboxMode[] = ['container', 'bwrap', 'host']

export interface SandboxSetting {
  company_id: number
  /** The company's choice; none: the deployment's default. */
  mode: SandboxMode | null
  effective_mode: SandboxMode
  default_mode: SandboxMode
  env: string[]
  limits: { image: string | null; network: boolean | string | null; cpus: number | string | null; memory_mb: number | null; pids_limit: number | null }
  cache: unknown
}

export interface ResearchSetting {
  company_id: number
  enabled: boolean
  roles: string[]
  hosts: string[]
  default_hosts: string[]
}

/** The backend's rules (`sandbox.valid_env_name`, `research_tools._HOST`), checked before sending; it checks again. */
export const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/
export const HOST_NAME = /^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$|^localhost$|^(\d{1,3}\.){3}\d{1,3}$/

const company = (companyId: number) => `/v1/companies/${companyId}`

export const executionApi = {
  sandbox: (companyId: number) => api.get<SandboxSetting>(`${company(companyId)}/sandbox`),
  setSandbox: (companyId: number, mode: SandboxMode | null, env: string[]) =>
    api.put<SandboxSetting>(`${company(companyId)}/sandbox`, { mode, env }),
  /** Asks the daemon; not idempotent in effect, never resent. */
  resetCache: (companyId: number) =>
    fetchApi<Record<string, unknown>>(`${company(companyId)}/sandbox/cache/reset`, { method: 'POST', body: '{}', once: true, timeoutMs: 90_000 }),
  research: (companyId: number) => api.get<ResearchSetting>(`${company(companyId)}/research`),
  setResearch: (companyId: number, hosts: string[]) => api.put<ResearchSetting>(`${company(companyId)}/research`, { hosts }),
}

const sandboxKey = (companyId: number | null) => ['sandbox', companyId] as const
const researchKey = (companyId: number | null) => ['research-hosts', companyId] as const

export function useSandbox(companyId: number | null) {
  return useQuery({ queryKey: sandboxKey(companyId), queryFn: () => executionApi.sandbox(companyId as number), enabled: companyId !== null })
}

export function useSetSandbox(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ mode, env }: { mode: SandboxMode | null; env: string[] }) => executionApi.setSandbox(companyId as number, mode, env),
    onSuccess: data => client.setQueryData(sandboxKey(companyId), data),
  })
}

export function useResetCache(companyId: number | null) {
  return useMutation({ mutationFn: () => executionApi.resetCache(companyId as number) })
}

export function useResearch(companyId: number | null) {
  return useQuery({ queryKey: researchKey(companyId), queryFn: () => executionApi.research(companyId as number), enabled: companyId !== null })
}

export function useSetResearch(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (hosts: string[]) => executionApi.setResearch(companyId as number, hosts),
    onSuccess: data => client.setQueryData(researchKey(companyId), data),
  })
}
