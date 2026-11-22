import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Save, Trash2, TriangleAlert } from 'lucide-react'
import { useGroupDecision, useGroupMappings, useSaveGroupMappings } from '../hooks/useSsoGroups'
import { errorDetail, type GroupMappingView, type GroupPair, type GroupRoleCap, type ManagerApproval } from '../services/api'
import { useAuthStore } from '../store/useAuthStore'
import { FormActions, Panel, SavedNote } from './ui'

/**
 * Identity-provider groups mapped to SSO-managed teams (issue #703,
 * docs/archive/design/SSO_GROUP_MAPPING.md). On each SSO sign-in the claim sets the
 * person's membership in the mapped teams only. A pair to a Manager team
 * waits for a second Owner; the cap is never Owner.
 */
export function SsoGroupMappings({ companyId }: { companyId: number | null }) {
  const { t } = useTranslation()
  const view = useGroupMappings(companyId, true)
  if (!view.data || !Array.isArray(view.data.mappings)) return null
  return (
    <div className="mt-4">
      <Panel title={t('sso.groups.title')}>
        <GroupForm key={`${view.data.groups_claim}:${view.data.mappings.length}:${view.data.group_role_cap}`}
          companyId={companyId} view={view.data} />
        <Approvals companyId={companyId} view={view.data} />
      </Panel>
    </div>
  )
}

function GroupForm({ companyId, view }: { companyId: number | null; view: GroupMappingView }) {
  const { t } = useTranslation()
  const save = useSaveGroupMappings(companyId)
  const [enabled, setEnabled] = useState(view.groups_enabled)
  const [claim, setClaim] = useState(view.groups_claim)
  const [cap, setCap] = useState<GroupRoleCap>(view.group_role_cap)
  const [rows, setRows] = useState<GroupPair[]>(view.mappings.map(m => ({ group_value: m.group_value, team_id: m.team_id })))
  const pending = view.approvals.filter(a => a.state === 'pending')
  const teams = view.sso_teams

  function update(index: number, patch: Partial<GroupPair>) {
    setRows(current => current.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  function submit() {
    // Pending proposals are sent again so a save keeps them; the backend never duplicates one.
    const proposals = pending.map(a => ({ group_value: a.group_value, team_id: a.team_id }))
    const pairs = [...rows.filter(r => r.group_value && r.team_id), ...proposals]
    save.mutate({ groups_enabled: enabled, groups_claim: claim.trim(), group_role_cap: cap, mappings: pairs })
  }

  return (
    <form className="space-y-3 p-3" aria-label={t('sso.groups.title')} onSubmit={e => { e.preventDefault(); submit() }}>
      <p className="text-xs text-gray-400">{t('sso.groups.intro')}</p>
      {!view.groups_available && <div role="note" className="alert-warning text-sm">{t('sso.groups.googleUnavailable')}</div>}
      {view.last_overage && (
        <div role="alert" className="alert-warning text-sm">
          {t('sso.groups.overage', {
            when: view.last_overage.observed_at ? new Date(view.last_overage.observed_at).toLocaleString() : '—',
            host: view.last_overage.host ?? '—',
          })}
        </div>
      )}
      {save.isError && <div role="alert" className="alert-error text-sm">{errorDetail(save.error)}</div>}
      <label className="flex items-center gap-2 text-[13px] text-gray-200">
        <input type="checkbox" className="accent-brand-500" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
        {t('sso.groups.enabled')}
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="input-label">{t('sso.groups.claim')}</span>
          <input className="input w-full font-mono" value={claim} maxLength={64} onChange={e => setClaim(e.target.value)} />
          {claim !== view.proposed_claim && (
            <span className="mt-1 block text-[11px] text-gray-400">{t('sso.groups.proposedClaim', { claim: view.proposed_claim })}</span>
          )}
        </label>
        <label className="block">
          <span className="input-label">{t('sso.groups.cap')}</span>
          <select className="select w-full" value={cap} onChange={e => setCap(e.target.value as GroupRoleCap)}>
            {view.caps.map(c => <option key={c} value={c}>{t(`teams.roles.${c}`)}</option>)}
          </select>
        </label>
      </div>
      {cap === 'Manager' && (
        <p role="note" className="flex items-start gap-1.5 text-xs text-amber-300">
          <TriangleAlert size={13} aria-hidden="true" className="mt-0.5 flex-shrink-0" />{t('sso.groups.capWarning')}
        </p>
      )}
      {view.compares_lower && <p className="text-xs text-gray-400">{t('sso.groups.entraGuids')}</p>}
      {teams.length === 0 ? (
        <p className="text-xs text-gray-400">{t('sso.groups.noSsoTeams')}</p>
      ) : (
        <ul className="space-y-2" aria-label={t('sso.groups.pairs')}>
          {rows.map((row, index) => (
            <li key={index} className="flex flex-wrap items-end gap-2">
              <label className="block min-w-[12rem] flex-1">
                <span className="input-label">{t('sso.groups.value')}</span>
                <input className="input w-full font-mono" value={row.group_value} maxLength={256}
                  onChange={e => update(index, { group_value: e.target.value })} />
              </label>
              <label className="block min-w-[10rem]">
                <span className="input-label">{t('sso.groups.team')}</span>
                <select className="select w-full" value={row.team_id || ''}
                  onChange={e => update(index, { team_id: Number(e.target.value) })}>
                  <option value="">{t('teams.pickMember')}</option>
                  {teams.map(team => <option key={team.id} value={team.id}>{`${team.name} (${t(`teams.roles.${team.role}`)})`}</option>)}
                </select>
              </label>
              <button type="button" className="btn-tool" aria-label={t('sso.groups.removePair')}
                onClick={() => setRows(current => current.filter((_, i) => i !== index))}>
                <Trash2 size={13} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <FormActions>
        {save.isSuccess && <SavedNote>{t('sso.groups.saved')}</SavedNote>}
        <button type="button" className="btn-tool border border-surface-700"
          disabled={!view.groups_available || teams.length === 0 || rows.length >= view.max_pairs}
          onClick={() => setRows(current => [...current, { group_value: '', team_id: 0 }])}>
          <Plus size={13} aria-hidden="true" />{t('sso.groups.addPair')}
        </button>
        <button type="submit" className="btn-primary btn-sm" disabled={save.isPending}>
          <Save size={13} aria-hidden="true" />{t('sso.groups.save')}
        </button>
      </FormActions>
    </form>
  )
}

function Approvals({ companyId, view }: { companyId: number | null; view: GroupMappingView }) {
  const { t } = useTranslation()
  const me = useAuthStore(state => state.user?.user_id ?? null)
  const decide = useGroupDecision(companyId)
  const pending = view.approvals.filter(a => a.state === 'pending')
  if (pending.length === 0) return null
  const names = new Map(view.sso_teams.map(team => [team.id, team.name]))
  return (
    <div className="space-y-2 border-t border-surface-700 p-3">
      <h3 className="text-[13px] font-medium text-gray-100">{t('sso.groups.pending')}</h3>
      <p className="text-xs text-gray-400">{t('sso.groups.fourEyes')}</p>
      {decide.isError && <div role="alert" className="alert-error text-sm">{errorDetail(decide.error)}</div>}
      <ul className="space-y-2">
        {pending.map(approval => (
          <PendingPair key={approval.id} approval={approval} teamName={names.get(approval.team_id) ?? `#${approval.team_id}`}
            mine={me === approval.requested_by} decide={decide} />
        ))}
      </ul>
    </div>
  )
}

function PendingPair({ approval, teamName, mine, decide }: {
  approval: ManagerApproval
  teamName: string
  mine: boolean
  decide: ReturnType<typeof useGroupDecision>
}) {
  const { t } = useTranslation()
  const [alone, setAlone] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const [reason, setReason] = useState('')
  const id = approval.id
  return (
    <li className="rounded border border-surface-700 p-2 text-[13px] text-gray-200" aria-label={approval.group_value}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span><code className="font-mono">{approval.group_value}</code> → {teamName}</span>
        <span className="flex gap-2">
          {mine ? (
            <>
              <button type="button" className="btn-tool" onClick={() => decide.mutate({ kind: 'withdraw', approvalId: id })}>
                {t('sso.groups.withdraw')}
              </button>
              <button type="button" className="btn-tool" onClick={() => setAlone(v => !v)}>{t('sso.groups.confirmAlone')}</button>
            </>
          ) : (
            <>
              <button type="button" className="btn-primary btn-sm" onClick={() => decide.mutate({ kind: 'approve', approvalId: id })}>
                {t('sso.groups.approve')}
              </button>
              <button type="button" className="btn-tool" onClick={() => decide.mutate({ kind: 'reject', approvalId: id, reason: null })}>
                {t('sso.groups.reject')}
              </button>
            </>
          )}
        </span>
      </div>
      {mine && alone && (
        <form className="mt-2 space-y-2" onSubmit={e => {
          e.preventDefault()
          decide.mutate({ kind: 'confirmAlone', approvalId: id, confirmation, reason: reason.trim() })
        }}>
          <p className="text-xs text-amber-300">{t('sso.groups.aloneWarning')}</p>
          <label className="block">
            <span className="input-label">{t('sso.groups.typeTeam', { name: teamName })}</span>
            <input className="input w-full" value={confirmation} onChange={e => setConfirmation(e.target.value)} />
          </label>
          <label className="block">
            <span className="input-label">{t('sso.groups.reason')}</span>
            <input className="input w-full" value={reason} maxLength={500} onChange={e => setReason(e.target.value)} />
          </label>
          <button type="submit" className="btn-primary btn-sm"
            disabled={confirmation !== teamName || reason.trim().length < 10 || decide.isPending}>
            {t('sso.groups.confirmAlone')}
          </button>
        </form>
      )}
    </li>
  )
}
