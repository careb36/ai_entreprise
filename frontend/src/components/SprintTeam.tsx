/**
 * A sprint's team: the roles its tasks may go to (backend
 * `services/sprint_team.py`). When a sprint is planned the PO suggests the
 * roles the request needs; the Manager confirms or changes them here, and
 * the sprint's tasks go only to that team. `null` means every role the
 * company hired.
 */
import { useTranslation } from 'react-i18next'
import { TEAM_SOURCES, useRoleLabel } from '../lib/sprintTeam'

export function SprintTeamPicker({
  hired,
  team,
  suggested,
  reasons,
  onChange,
}: {
  hired: string[]
  team: string[]
  suggested: string[]
  reasons: Record<string, string>
  onChange: (team: string[]) => void
}) {
  const { t } = useTranslation()
  const label = useRoleLabel()
  return (
    <fieldset className="space-y-1.5 rounded-lg border border-surface-700 bg-surface-900/70 p-2.5">
      <legend className="px-1 text-xs font-semibold text-gray-100">{t('sprintTeam.title')}</legend>
      <p className="text-[11px] text-gray-500">{t('sprintTeam.hint')}</p>
      {hired.map(role => (
        <label key={role} className="flex items-start gap-2 text-xs text-gray-300">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={team.includes(role)}
            disabled={team.length === 1 && team.includes(role)}
            onChange={e => onChange(e.target.checked ? hired.filter(r => r === role || team.includes(r)) : team.filter(r => r !== role))}
          />
          <span className="min-w-0">
            {label(role)}
            {suggested.includes(role) && (
              <span className="ml-1.5 text-[10px] text-brand-300">{t('sprintTeam.suggested')}</span>
            )}
            {reasons[role] && (
              // The PO's reason: a claim for the Manager to weigh.
              <span className="block text-[11px] text-sky-200/80">{t('sprintTeam.reason', { why: reasons[role] })}</span>
            )}
          </span>
        </label>
      ))}
    </fieldset>
  )
}

/** The team as information: on the sprint page, and in the planner in automatic mode. */
export function SprintTeamChips({ team, source }: { team?: string[] | null; source?: string | null }) {
  const { t } = useTranslation()
  const label = useRoleLabel()
  return (
    <div className="space-y-0.5">
      <p className="flex flex-wrap items-center gap-1 text-[11px] text-gray-400" aria-label={t('sprintTeam.sprintLabel')}>
        <span>{t('sprintTeam.sprintLabel')}</span>
        {team && team.length > 0
          ? team.map(role => (
              <span key={role} className="rounded-full border border-surface-700 bg-surface-900 px-2 py-0.5 text-gray-300">{label(role)}</span>
            ))
          : <span className="text-gray-500">{t('sprintTeam.everyRole')}</span>}
      </p>
      {source && TEAM_SOURCES.includes(source) && (
        // Who decided the team: evidence, not decoration.
        <p className="text-[11px] text-gray-500">{t(`sprintTeam.source.${source}`)}</p>
      )}
    </div>
  )
}
