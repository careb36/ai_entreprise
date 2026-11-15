import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type TeamInput, type UsageQuery } from '../services/api'

const teamsKey = (companyId: number | null) => ['teams', companyId] as const
const usageKey = (companyId: number | null, query: UsageQuery) =>
  ['usage', companyId, query.since, query.until, query.period] as const

/** The company's teams and members, for its Owners. */
export function useTeams(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: teamsKey(companyId),
    queryFn: () => api.getTeams(companyId as number),
    enabled: enabled && companyId !== null,
  })
}

type TeamChange =
  | { kind: 'create'; data: TeamInput }
  | { kind: 'update'; teamId: number; data: Partial<TeamInput> }
  | { kind: 'delete'; teamId: number }
  | { kind: 'add'; teamId: number; userId: number }
  | { kind: 'remove'; teamId: number; userId: number }

/** One mutation for every team change; each refreshes the list. */
export function useTeamChange(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (change: TeamChange): Promise<unknown> => {
      const id = companyId as number
      switch (change.kind) {
        case 'create': return api.createTeam(id, change.data)
        case 'update': return api.updateTeam(id, change.teamId, change.data)
        case 'delete': return api.deleteTeam(id, change.teamId)
        case 'add': return api.addTeamMember(id, change.teamId, change.userId)
        case 'remove': return api.removeTeamMember(id, change.teamId, change.userId)
      }
    },
    onSuccess: () => client.invalidateQueries({ queryKey: teamsKey(companyId) }),
  })
}

/** The usage report, for Managers (a team can make one). */
export function useUsage(companyId: number | null, query: UsageQuery, enabled: boolean) {
  return useQuery({
    queryKey: usageKey(companyId, query),
    queryFn: () => api.getUsage(companyId as number, query),
    enabled: enabled && companyId !== null && Boolean(query.since && query.until),
  })
}

/** The same report as CSV: audited by the backend on every export. */
export function useUsageExport(companyId: number | null) {
  return useMutation({
    mutationFn: (query: UsageQuery) => api.exportUsageCsv(companyId as number, query),
  })
}
