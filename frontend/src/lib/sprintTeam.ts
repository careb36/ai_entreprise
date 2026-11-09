/** Helpers for a sprint's team (components/SprintTeam.tsx). */
import { useTranslation } from 'react-i18next'

/** A role's display name: the catalogue's label, else the stored name. */
export function useRoleLabel(): (role: string) => string {
  const { t } = useTranslation()
  return (role: string) => t(`teamRoles.${role}`, { defaultValue: role })
}

/** Whether every task's assignee is on *team*: otherwise the plan must be made again. */
export function planFitsTeam(team: string[], assignees: string[]): boolean {
  return assignees.every(role => team.includes(role))
}

/** The same roles, whatever the order. */
export function sameTeam(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every(role => b.includes(role))
}

/** How a sprint's team was chosen (backend `sprint_team.TEAM_SOURCES`). */
export const TEAM_SOURCES = ['suggested', 'person', 'automatic', 'all']
