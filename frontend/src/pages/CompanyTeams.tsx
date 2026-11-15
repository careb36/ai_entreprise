import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Trash2, UsersRound } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { useTeamChange, useTeams } from '../hooks/useTeams'
import { errorDetail, type Team, type TeamCandidate, type TeamRole } from '../services/api'
import { useAppStore } from '../store/useAppStore'

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
    <div className="page-container max-w-3xl">
      <div className="page-header mb-6">
        <div>
          <div className="flex items-center gap-2">
            <UsersRound className="text-brand-400" size={22} />
            <h1 className="page-title">{t('teams.title')}</h1>
          </div>
          <p className="text-gray-400 mt-1">{t('teams.subtitle')}</p>
        </div>
      </div>
      {role !== 'Owner' ? (
        <div role="status" className="card p-4 text-sm text-gray-300">
          {t('teams.ownerOnly', { role: role ?? '—' })}
        </div>
      ) : teams.isLoading ? (
        <Loader2 className="animate-spin text-brand-400" size={20} />
      ) : teams.isError ? (
        <div role="alert" className="alert-error text-sm">{errorDetail(teams.error)}</div>
      ) : teams.data ? (
        <div className="space-y-4">
          {change.isError && <div role="alert" className="alert-error text-sm">{errorDetail(change.error)}</div>}
          <NewTeam change={change} />
          {teams.data.teams.length === 0 ? (
            <p className="text-sm text-gray-400">{t('teams.none')}</p>
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
    <form onSubmit={e => { e.preventDefault(); submit() }} className="card p-4 space-y-3" aria-label={t('teams.newTeam')}>
      <h2 className="text-sm font-semibold text-gray-200">{t('teams.newTeam')}</h2>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block sm:col-span-1">
          <span className="input-label">{t('teams.name')}</span>
          <input className="input w-full" value={name} maxLength={80} onChange={e => setName(e.target.value)} />
        </label>
        <label className="block sm:col-span-1">
          <span className="input-label">{t('teams.description')}</span>
          <input className="input w-full" value={description} maxLength={500} onChange={e => setDescription(e.target.value)} />
        </label>
        <label className="block sm:col-span-1">
          <span className="input-label">{t('teams.role')}</span>
          <select className="select w-full" value={role} onChange={e => setRole(e.target.value as TeamRole)}>
            {ROLES.map(r => <option key={r} value={r}>{t(`teams.roles.${r}`)}</option>)}
          </select>
        </label>
      </div>
      <p className="text-xs text-gray-400">{t('teams.roleHint')}</p>
      <button type="submit" className="btn-primary btn-sm" disabled={!name.trim() || change.isPending}>
        {t('teams.create')}
      </button>
    </form>
  )
}

function TeamCard({ team, members, change }: { team: Team; members: TeamCandidate[]; change: Change }) {
  const { t } = useTranslation()
  const [adding, setAdding] = useState('')
  const inTeam = members.filter(m => team.members.includes(m.user_id))
  const outside = members.filter(m => !team.members.includes(m.user_id))

  return (
    <section className="card p-4 space-y-3" aria-label={team.name}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold text-gray-100">{team.name}</h2>
          {team.description && <p className="text-sm text-gray-400">{team.description}</p>}
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-sm">
            <span className="text-gray-400">{t('teams.role')}</span>
            <select className="select" value={team.role}
              onChange={e => change.mutate({ kind: 'update', teamId: team.id, data: { role: e.target.value as TeamRole } })}>
              {ROLES.map(r => <option key={r} value={r}>{t(`teams.roles.${r}`)}</option>)}
            </select>
          </label>
          <button type="button" className="btn-ghost btn-sm" aria-label={t('teams.delete', { name: team.name })}
            onClick={() => { if (window.confirm(t('teams.confirmDelete', { name: team.name }))) change.mutate({ kind: 'delete', teamId: team.id }) }}>
            <Trash2 size={14} />
          </button>
        </div>
      </div>
      {inTeam.length === 0 ? (
        <p className="text-sm text-gray-400">{t('teams.noMembers')}</p>
      ) : (
        <ul className="space-y-1">
          {inTeam.map(m => (
            <li key={m.user_id} className="flex items-center justify-between gap-2 text-sm">
              <span className="text-gray-200">{m.full_name || m.email}</span>
              <button type="button" className="btn-ghost btn-sm"
                onClick={() => change.mutate({ kind: 'remove', teamId: team.id, userId: m.user_id })}>
                {t('teams.remove')}
              </button>
            </li>
          ))}
        </ul>
      )}
      {outside.length > 0 && (
        <form className="flex items-end gap-2" onSubmit={e => {
          e.preventDefault()
          if (!adding) return
          change.mutate({ kind: 'add', teamId: team.id, userId: Number(adding) }, { onSuccess: () => setAdding('') })
        }}>
          <label className="block flex-1">
            <span className="input-label">{t('teams.addMember')}</span>
            <select className="select w-full" value={adding} onChange={e => setAdding(e.target.value)}>
              <option value="">{t('teams.pickMember')}</option>
              {outside.map(m => <option key={m.user_id} value={m.user_id}>{m.full_name || m.email}</option>)}
            </select>
          </label>
          <button type="submit" className="btn-secondary btn-sm" disabled={!adding || change.isPending}>{t('teams.add')}</button>
        </form>
      )}
    </section>
  )
}

function MemberRoles({ members, teams }: { members: TeamCandidate[]; teams: Team[] }) {
  const { t } = useTranslation()
  const names = new Map(teams.map(team => [team.id, team.name]))
  return (
    <section className="card p-4" aria-labelledby="member-roles">
      <h2 id="member-roles" className="text-sm font-semibold text-gray-200 mb-2">{t('teams.memberRoles')}</h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-400">
            <th className="py-1 font-normal">{t('teams.member')}</th>
            <th className="py-1 font-normal">{t('teams.ownRole')}</th>
            <th className="py-1 font-normal">{t('teams.effectiveRole')}</th>
            <th className="py-1 font-normal">{t('teams.inTeams')}</th>
          </tr>
        </thead>
        <tbody>
          {members.map(m => (
            <tr key={m.user_id} className="border-t border-surface-800">
              <td className="py-1 text-gray-200">{m.full_name || m.email}</td>
              <td className="py-1">{m.human_role}</td>
              <td className="py-1">{m.effective_role ?? '—'}</td>
              <td className="py-1 text-gray-400">{m.team_ids.map(id => names.get(id) ?? `#${id}`).join(', ') || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
