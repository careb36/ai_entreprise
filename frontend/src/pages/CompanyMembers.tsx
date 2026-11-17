import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Trash2, UserPlus, Users } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { useCompanyInvitations, useInvite, useRevokeInvitation } from '../hooks/useInvitations'
import { ApiError, errorDetail, type CompanyRole } from '../services/api'
import { useAppStore } from '../store/useAppStore'

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

  return (
    <div className="page-container max-w-2xl space-y-4">
      <div className="page-header mb-2">
        <div>
          <div className="flex items-center gap-2">
            <Users className="text-brand-400" size={22} />
            <h1 className="page-title">{t('members.title')}</h1>
          </div>
          <p className="text-gray-400 mt-1">
            {t('members.subtitle', { company: companyName || companyId })}
          </p>
        </div>
      </div>

      {!canInvite ? (
        <div role="status" className="card p-4 text-sm text-gray-300">
          {t('members.managersOnly', { role: role ?? '—' })}
        </div>
      ) : (
        <>
          <form onSubmit={e => { e.preventDefault(); submit() }} className="card p-4 space-y-4" aria-labelledby="invite-title">
            <h2 id="invite-title" className="text-sm font-semibold text-white">{t('members.inviteTitle')}</h2>
            <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
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
            <p className="text-xs text-gray-500">{t(`members.roleHints.${humanRole}`)}</p>
            <div className="flex flex-wrap items-center gap-3">
              <button type="submit" disabled={invite.isPending || !email.trim()} className="btn btn-primary">
                {invite.isPending ? <Loader2 size={15} className="animate-spin" /> : <UserPlus size={15} />}
                {invite.isPending ? t('members.inviting') : t('members.invite')}
              </button>
            </div>
            {invited && (
              <div role="status" className="space-y-2 text-sm text-status-success">
                <p>{t('members.invited', { email: invited.email, role: t(`members.roles.${invited.role}`) })}</p>
                <label className="block text-gray-300">
                  <span className="input-label">{t('members.inviteLink')}</span>
                  <input readOnly value={invited.link} className="input w-full" onFocus={e => e.target.select()} />
                </label>
              </div>
            )}
            {invite.isError && (
              <div role="alert" className="alert-error flex flex-wrap items-center gap-2 text-sm">
                <span>{errorMessage}</span>
                <button type="button" onClick={submit} className="underline">{t('common.retry')}</button>
              </div>
            )}
          </form>

          <section className="card p-4 space-y-3" aria-labelledby="pending-title">
            <h2 id="pending-title" className="text-sm font-semibold text-white">{t('members.pendingTitle')}</h2>
            {(pending.data?.invitations ?? []).length === 0 ? (
              <p className="text-sm text-gray-400">{t('members.pendingNone')}</p>
            ) : (
              <ul className="space-y-2">
                {pending.data?.invitations.map(row => (
                  <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 text-sm text-gray-200">
                    <span>{t('members.pendingRow', { email: row.email, role: t(`members.roles.${row.human_role}`) })}</span>
                    <button type="button" className="btn btn-ghost" disabled={revoke.isPending}
                      aria-label={t('members.revokeLabel', { email: row.email })}
                      onClick={() => revoke.mutate(row.id)}>
                      <Trash2 size={14} />{t('members.revoke')}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {revoke.isError && (
              <div role="alert" className="alert-error text-sm">
                {t('members.revokeError', { message: errorDetail(revoke.error) })}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}
