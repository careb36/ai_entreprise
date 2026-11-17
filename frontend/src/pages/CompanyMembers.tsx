import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { MailPlus, Trash2, UserPlus } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { useCompanyInvitations, useInvite, useRevokeInvitation } from '../hooks/useInvitations'
import { ApiError, errorDetail, type CompanyRole } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { CountPill, EmptyState, PageHeader, Panel } from '../components/ui'
import { AccessNotice, ButtonSpinner, FormActions } from './admin/AdminParts'

const ROLES: CompanyRole[] = ['Viewer', 'Manager', 'Owner']
const RANK: Record<CompanyRole, number> = { Viewer: 1, Manager: 2, Owner: 3 }

/**
 * Company members: a Manager or an Owner invites an address
 * (`POST /v1/company/invite`), up to their own role. Inviting adds nobody:
 * the person accepts once signed in with that address. The answer is the
 * same whether or not the address has an account.
 */
export default function CompanyMembers() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const companyName = useAppStore(state => state.companyName)
  const role = useCurrentRole()
  const canInvite = role === 'Owner' || role === 'Manager'
  const invite = useInvite(companyId)
  const pending = useCompanyInvitations(companyId, canInvite)
  const revoke = useRevokeInvitation(companyId)
  const [email, setEmail] = useState('')
  const [humanRole, setHumanRole] = useState<CompanyRole>('Viewer')
  const [invited, setInvited] = useState<{ email: string; role: CompanyRole; link: string } | null>(null)
  const roles = ROLES.filter(value => role && RANK[value] <= RANK[role as CompanyRole])

  function submit() {
    const address = email.trim()
    if (!address || companyId === null) return
    setInvited(null)
    invite.mutate(
      { company_id: companyId, email: address, human_role: humanRole },
      {
        onSuccess: answer => {
          const link = `${window.location.origin}/invitations?token=${encodeURIComponent(answer.token)}`
          setInvited({ email: address, role: humanRole, link })
          setEmail('')
        },
      },
    )
  }

  const status = invite.error instanceof ApiError ? invite.error.status : null
  const errorMessage = status === 403
    ? t('members.roleTooHigh')
    : t('members.inviteError', { message: errorDetail(invite.error) })

  const invitations = pending.data?.invitations ?? []

  return (
    <div className="page-container max-w-3xl space-y-4">
      <PageHeader title={t('members.title')} subtitle={t('members.subtitle', { company: companyName || companyId })} />

      {!canInvite ? (
        <AccessNotice>{t('members.managersOnly', { role: role ?? '—' })}</AccessNotice>
      ) : (
        <>
          <Panel title={t('members.inviteTitle')}>
            <form onSubmit={e => { e.preventDefault(); submit() }} className="space-y-3 p-3" aria-label={t('members.inviteTitle')}>
              <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
                <label className="block">
                  <span className="input-label">{t('members.email')}</span>
                  <input type="email" required value={email} onChange={e => setEmail(e.target.value)}
                    placeholder={t('members.emailPlaceholder')} className="input w-full" />
                </label>
                <label className="block">
                  <span className="input-label">{t('members.role')}</span>
                  <select value={humanRole} onChange={e => setHumanRole(e.target.value as CompanyRole)} className="select w-full">
                    {roles.map(value => (
                      <option key={value} value={value}>{t(`members.roles.${value}`)}</option>
                    ))}
                  </select>
                </label>
              </div>
              <p className="text-xs text-gray-400">{t(`members.roleHints.${humanRole}`)}</p>
              {invited && (
                <div role="status" className="space-y-2 rounded-md border border-emerald-700/50 bg-emerald-950/30 p-3 text-sm text-status-success">
                  <p>{t('members.invited', { email: invited.email, role: t(`members.roles.${invited.role}`) })}</p>
                  <label className="block text-gray-300">
                    <span className="input-label">{t('members.inviteLink')}</span>
                    <input readOnly value={invited.link} className="input w-full font-mono text-xs" onFocus={e => e.target.select()} />
                  </label>
                </div>
              )}
              {invite.isError && (
                <div role="alert" className="alert-error items-center text-sm">
                  <span className="min-w-0 flex-1">{errorMessage}</span>
                  <button type="button" onClick={submit} className="btn-tool">{t('common.retry')}</button>
                </div>
              )}
              <FormActions>
                <button type="submit" disabled={invite.isPending || !email.trim()} className="btn-primary btn-sm">
                  {invite.isPending ? <ButtonSpinner /> : <UserPlus size={13} aria-hidden="true" />}
                  {invite.isPending ? t('members.inviting') : t('members.invite')}
                </button>
              </FormActions>
            </form>
          </Panel>

          <Panel title={t('members.pendingTitle')}
            count={<CountPill count={invitations.length} label={t('members.pendingCount', { count: invitations.length })} />}>
            {invitations.length === 0 ? (
              <EmptyState icon={MailPlus} title={t('members.pendingNone')} />
            ) : (
              <ul className="data-list">
                {invitations.map(row => (
                  <li key={row.id} className="data-row flex flex-wrap items-center justify-between gap-2">
                    <span className="min-w-0 text-[13px] text-gray-100">
                      {t('members.pendingRow', { email: row.email, role: t(`members.roles.${row.human_role}`) })}
                    </span>
                    <button type="button" className="btn-tool hover:text-red-300" disabled={revoke.isPending}
                      aria-label={t('members.revokeLabel', { email: row.email })}
                      onClick={() => revoke.mutate(row.id)}>
                      <Trash2 size={13} aria-hidden="true" />{t('members.revoke')}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {revoke.isError && (
              <div className="border-t border-surface-700 p-3">
                <div role="alert" className="alert-error text-sm">
                  {t('members.revokeError', { message: errorDetail(revoke.error) })}
                </div>
              </div>
            )}
          </Panel>
        </>
      )}
    </div>
  )
}
