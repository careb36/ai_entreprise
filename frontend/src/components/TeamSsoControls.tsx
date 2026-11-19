import { useTranslation } from 'react-i18next'
import { ShieldOff, UserMinus } from 'lucide-react'
import { useMembershipSourcePreview, type useTeamChange } from '../hooks/useTeams'
import { errorDetail, type Team, type TeamCandidate } from '../services/api'

type Change = ReturnType<typeof useTeamChange>

/** The reason an Owner gives for a break-glass act: 10 to 500 characters, asked for each time. */
function askReason(question: string): string | null {
  const answer = window.prompt(question)?.trim() ?? ''
  return answer.length >= 10 ? answer.slice(0, 500) : null
}

/**
 * Who manages a team's members (issue #703): the Owner by hand, or the
 * identity provider's groups. Converting a team with members shows who is kept
 * as a manual exception and asks for a confirmation of exactly that preview.
 */
export function TeamSourceSwitch({ team, change }: { team: Team; change: Change }) {
  const { t } = useTranslation()
  const preview = useMembershipSourcePreview(team.company_id)
  const sso = team.membership_source === 'sso'

  function toSso() {
    if (team.members.length === 0) {
      change.mutate({ kind: 'source', teamId: team.id, data: { membership_source: 'sso' } })
      return
    }
    preview.mutate(team.id, {
      onSuccess: shown => {
        if (!window.confirm(t('teams.sso.confirmConvert', { name: team.name, count: shown.members.length }))) return
        change.mutate({
          kind: 'source',
          teamId: team.id,
          data: { membership_source: 'sso', preview_id: shown.id, confirm: true },
        })
      },
    })
  }

  function toOwner() {
    if (window.confirm(t('teams.sso.confirmBack', { name: team.name }))) {
      change.mutate({ kind: 'source', teamId: team.id, data: { membership_source: 'owner' } })
    }
  }

  return (
    <>
      <span className={`rounded px-1.5 py-0.5 text-[11px] ${sso ? 'bg-brand-500/20 text-brand-200' : 'bg-surface-700 text-gray-300'}`}>
        {t(sso ? 'teams.sso.managedBySso' : 'teams.sso.managedByOwner')}
      </span>
      <button type="button" className="btn-tool" disabled={change.isPending || preview.isPending} onClick={sso ? toOwner : toSso}>
        {t(sso ? 'teams.sso.switchToOwner' : 'teams.sso.switchToSso')}
      </button>
      {preview.isError && <span role="alert" className="text-[11px] text-red-300">{errorDetail(preview.error)}</span>}
    </>
  )
}

/** A member's action in an SSO-managed team: end a manual exception, or exclude (break-glass). */
export function SsoMemberAction({ team, member, change }: { team: Team; member: TeamCandidate; change: Change }) {
  const { t } = useTranslation()
  const name = member.full_name || member.email
  const exception = (team.manual_exceptions ?? []).some(e => e.user_id === member.user_id)
  if (exception) {
    return (
      <span className="flex items-center gap-2">
        <span className="text-[11px] text-amber-300">{t('teams.sso.manualException')}</span>
        <button type="button" className="btn-tool"
          onClick={() => change.mutate({ kind: 'remove', teamId: team.id, userId: member.user_id })}>
          <UserMinus size={13} aria-hidden="true" />{t('teams.sso.endException')}
        </button>
      </span>
    )
  }
  return (
    <button type="button" className="btn-tool hover:text-red-300" onClick={() => {
      const reason = askReason(t('teams.sso.excludeReason', { name }))
      if (reason) change.mutate({ kind: 'exclude', teamId: team.id, userId: member.user_id, reason })
    }}>
      <ShieldOff size={13} aria-hidden="true" />{t('teams.sso.exclude')}
    </button>
  )
}

/** The exclusions in force on a team: the mapping never adds these people back until lifted. */
export function TeamExclusions({ team, members, change }: { team: Team; members: TeamCandidate[]; change: Change }) {
  const { t } = useTranslation()
  const exclusions = team.exclusions ?? []
  if (exclusions.length === 0) return null
  const names = new Map(members.map(m => [m.user_id, m.full_name || m.email]))
  return (
    <div className="border-t border-surface-700 px-3 py-2">
      <p className="text-xs text-gray-400">{t('teams.sso.exclusions')}</p>
      <ul className="mt-1 space-y-1">
        {exclusions.map(ex => (
          <li key={ex.id} className="flex flex-wrap items-center justify-between gap-2 text-[13px] text-gray-200">
            <span>{names.get(ex.user_id) ?? `#${ex.user_id}`} · <span className="text-gray-400">{ex.reason}</span></span>
            <button type="button" className="btn-tool" onClick={() => {
              const reason = askReason(t('teams.sso.liftReason', { name: names.get(ex.user_id) ?? `#${ex.user_id}` }))
              if (reason) change.mutate({ kind: 'lift', teamId: team.id, exclusionId: ex.id, reason })
            }}>
              {t('teams.sso.lift')}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
