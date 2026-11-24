/**
 * Two settings of how the company's project is built and staffed
 * (`routers/sprint_control.py`, `routers/sprint_planning.py`): the stack
 * profile's overrides of what the detection finds (commands, port, probe
 * path, stack; an empty command turns the step off) and how a sprint's team
 * is chosen (suggested by the PO and confirmed by a Manager, or automatic).
 * Every member reads them; a Manager changes them.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'

export const COMMAND_FIELDS = ['install', 'test', 'lint', 'start', 'e2e'] as const
export type CommandField = typeof COMMAND_FIELDS[number]

/** The company's overrides; `folders`, `every_delivery` and `programs` are kept as they are when the root is edited. */
export interface StackOverrides {
  stack?: string
  install?: string
  test?: string
  lint?: string
  start?: string
  e2e?: string
  port?: number
  probe_path?: string
  web?: boolean
  folders?: Record<string, unknown>
  every_delivery?: unknown[]
  /** The programs a Manager declared (`workspace__run_program`); edited through the API for now. */
  programs?: unknown[]
}

export interface StackProfileSetting {
  company_id: number
  overrides: StackOverrides
  /** What the profile resolves to before detection, each field with its source. */
  effective_without_detection: Record<string, unknown>
  stacks: { stack: string; manifests?: string[] }[]
  installed_manifests: Record<string, unknown>
  folders_without_detection: unknown
  every_delivery: unknown[]
}

export type TeamSelection = 'suggested' | 'automatic'
export const TEAM_SELECTIONS: TeamSelection[] = ['suggested', 'automatic']

const company = (companyId: number) => `/v1/companies/${companyId}`

export const projectSettingsApi = {
  stack: (companyId: number) => api.get<StackProfileSetting>(`${company(companyId)}/stack_profile`),
  setStack: (companyId: number, profile: StackOverrides) => api.put<StackProfileSetting>(`${company(companyId)}/stack_profile`, { profile }),
  team: (companyId: number) => api.get<{ company_id: number; team_selection: TeamSelection }>(`${company(companyId)}/team_selection`),
  setTeam: (companyId: number, teamSelection: TeamSelection) =>
    api.put<{ company_id: number; team_selection: TeamSelection }>(`${company(companyId)}/team_selection`, { team_selection: teamSelection }),
}

const stackKey = (companyId: number | null) => ['stack-profile', companyId] as const
const teamKey = (companyId: number | null) => ['team-selection', companyId] as const

export function useStackProfile(companyId: number | null) {
  return useQuery({ queryKey: stackKey(companyId), queryFn: () => projectSettingsApi.stack(companyId as number), enabled: companyId !== null })
}

export function useSetStackProfile(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (profile: StackOverrides) => projectSettingsApi.setStack(companyId as number, profile),
    onSuccess: data => client.setQueryData(stackKey(companyId), data),
  })
}

export function useTeamSelection(companyId: number | null) {
  return useQuery({ queryKey: teamKey(companyId), queryFn: () => projectSettingsApi.team(companyId as number), enabled: companyId !== null })
}

export function useSetTeamSelection(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (value: TeamSelection) => projectSettingsApi.setTeam(companyId as number, value),
    onSuccess: data => client.setQueryData(teamKey(companyId), data),
  })
}
