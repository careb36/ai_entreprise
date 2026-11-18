import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Trash2, UserMinus, UsersRound } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { useTeamChange, useTeams } from '../hooks/useTeams'
import { errorDetail, type Team, type TeamCandidate, type TeamRole } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { AccessNotice, CountPill, EmptyState, FormActions, LoadError, LoadingRows, PageHeader, Panel, TABLE, TD, TH, TR } from '../components/ui'

const ROLES: TeamRole[] = ['Viewer', 'Manager']

/**
 * The company's teams, for its Owners: a team's role (Viewer or Manager,
 * never Owner) applies to its members here, so access can be granted to a
 * team rather than person by person. A member's effective role is the higher
 * of their own and their teams'. Every change is audited by the backend.
 */
export default function CompanyTeams() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const teams = useTeams(companyId, role === 'Owner')
  const change = useTeamChange(companyId)

  return (
    <div className="page-container max-w-4xl space-y-4">
      <PageHeader title={t('teams.title')} subtitle={t('teams.subtitle')} />
      {role !== 'Owner' ? (
        <AccessNotice>{t('teams.ownerOnly', { role: role ?? '—' })}</AccessNotice>
      ) : teams.isLoading ? (
        <LoadingRows rows={3} />
      ) : teams.isError ? (
        <LoadError onRetry={() => teams.refetch()}>{errorDetail(teams.error)}</LoadError>
      ) : teams.data ? (
        <div className="space-y-4">
          {change.isError && <div role="alert" className="alert-error text-sm">{errorDetail(change.error)}</div>}
          <NewTeam change={change} />
          {teams.data.teams.length === 0 ? (
            <EmptyState icon={UsersRound} title={t('teams.none')} dashed />
          ) : (
            teams.data.teams.map(team => (
              <TeamCard key={team.id} team={team} members={teams.data.members} change={change} />
            ))
          )}
          <MemberRoles members={teams.data.members} teams={teams.data.teams} />
        </div>
      ) : null}
    </div>
  )
}

type Change = ReturnType<typeof useTeamChange>

function NewTeam({ change }: { change: Change }) {
  const { t } = useTranslation()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [role, setRole] = useState<TeamRole>('Viewer')

  function submit() {
    if (!name.trim()) return
    change.mutate(
      { kind: 'create', data: { name: name.trim(), description: description.trim() || null, role } },
      { onSuccess: () => { setName(''); setDescription(''); setRole('Viewer') } },
    )
  }

  return (
    <Panel title={t('teams.newTeam')}>
      <form onSubmit={e => { e.preventDefault(); submit() }} className="space-y-3 p-3" aria-label={t('teams.newTeam')}>
        <div className="grid gap-3 sm:grid-cols-[1fr_1.5fr_10rem]">
          <label className="block">
            <span className="input-label">{t('teams.name')}</span>
            <input className="input w-full" value={name} maxLength={80} onChange={e => setName(e.target.value)} />
          </label>
          <label className="block">
            <span className="input-label">{t('teams.description')}</span>
            <input className="input w-full" value={description} maxLength={500} onChange={e => setDescription(e.target.value)} />
          </label>
          <label className="block">
            <span className="input-label">{t('teams.role')}</span>
            <select className="select w-full" value={role} onChange={e => setRole(e.target.value as TeamRole)}>
              {ROLES.map(r => <option key={r} value={r}>{t(`teams.roles.${r}`)}</option>)}
            </select>
          </label>
        </div>
        <p className="text-xs text-gray-400">{t('teams.roleHint')}</p>
        <FormActions>
          <button type="submit" className="btn-primary btn-sm" disabled={!name.trim() || change.isPending}>
            {t('teams.create')}
          </button>
        </FormActions>
      </form>
    </Panel>
  )
}

function TeamCard({ team, members, change }: { team: Team; members: TeamCandidate[]; change: Change }) {
  const { t } = useTranslation()
  const [adding, setAdding] = useState('')
  const inTeam = members.filter(m => team.members.includes(m.user_id))
  const outside = members.filter(m => !team.members.includes(m.user_id))

  return (
    <Panel
      title={team.name}
      count={<CountPill count={inTeam.length} label={t('teams.memberCount', { count: inTeam.length })} />}
      actions={<>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          {t('teams.role')}
          <select className="select control-sm w-auto pr-7" value={team.role}
            onChange={e => change.mutate({ kind: 'update', teamId: team.id, data: { role: e.target.value as TeamRole } })}>
            {ROLES.map(r => <option key={r} value={r}>{t(`teams.roles.${r}`)}</option>)}
          </select>
        </label>
        <button type="button" className="btn-tool hover:text-red-300" aria-label={t('teams.delete', { name: team.name })}
          title={t('teams.delete', { name: team.name })}
          onClick={() => { if (window.confirm(t('teams.confirmDelete', { name: team.name }))) change.mutate({ kind: 'delete', teamId: team.id }) }}>
          <Trash2 size={13} aria-hidden="true" />
        </button>
      </>}>
      {team.description && <p className="border-b border-surface-700 px-3 py-2 text-xs text-gray-400">{team.description}</p>}
      {inTeam.length === 0 ? (
        <p className="px-3 py-3 text-xs text-gray-400">{t('teams.noMembers')}</p>
      ) : (
        <ul className="data-list">
          {inTeam.map(m => (
            <li key={m.user_id} className="data-row flex items-center justify-between gap-2 py-1.5">
              <span className="text-[13px] text-gray-100">{m.full_name || m.email}</span>
              <button type="button" className="btn-tool"
                onClick={() => change.mutate({ kind: 'remove', teamId: team.id, userId: m.user_id })}>
                <UserMinus size={13} aria-hidden="true" />{t('teams.remove')}
              </button>
            </li>
          ))}
        </ul>
      )}
      {outside.length > 0 && (
        <form className="flex items-end gap-2 border-t border-surface-700 p-3" onSubmit={e => {
          e.preventDefault()
          if (!adding) return
          change.mutate({ kind: 'add', teamId: team.id, userId: Number(adding) }, { onSuccess: () => setAdding('') })
        }}>
          <label className="block flex-1">
            <span className="input-label">{t('teams.addMember')}</span>
            <select className="select control-sm w-full" value={adding} onChange={e => setAdding(e.target.value)}>
              <option value="">{t('teams.pickMember')}</option>
              {outside.map(m => <option key={m.user_id} value={m.user_id}>{m.full_name || m.email}</option>)}
            </select>
          </label>
          <button type="submit" className="btn-tool border border-surface-700" disabled={!adding || change.isPending}>{t('teams.add')}</button>
        </form>
      )}
    </Panel>
  )
}

function MemberRoles({ members, teams }: { members: TeamCandidate[]; teams: Team[] }) {
  const { t } = useTranslation()
  const names = new Map(teams.map(team => [team.id, team.name]))
  return (
    <Panel title={t('teams.memberRoles')}
      count={<CountPill count={members.length} label={t('teams.memberCount', { count: members.length })} />}>
      <div className="overflow-x-auto">
        <table className={TABLE}>
          <thead>
            <tr>
              <th className={TH}>{t('teams.member')}</th>
              <th className={TH}>{t('teams.ownRole')}</th>
              <th className={TH}>{t('teams.effectiveRole')}</th>
              <th className={TH}>{t('teams.inTeams')}</th>
            </tr>
          </thead>
          <tbody>
            {members.map(m => (
              <tr key={m.user_id} className={TR}>
                <td className={`${TD} text-[13px] text-gray-100`}>{m.full_name || m.email}</td>
                <td className={TD}>{m.human_role}</td>
                <td className={TD}>{m.effective_role ?? '—'}</td>
                <td className={`${TD} text-gray-400`}>{m.team_ids.map(id => names.get(id) ?? `#${id}`).join(', ') || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
