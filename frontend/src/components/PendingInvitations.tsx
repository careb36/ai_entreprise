import { useTranslation } from 'react-i18next'
import { Check, Loader2, Mail, X } from 'lucide-react'
import { useMyInvitations, useRespondToInvitation } from '../hooks/useInvitations'
import { errorDetail } from '../services/api'

/**
 * The signed-in person's pending invitations, each to accept or decline.
 * Renders nothing while there are none, unless `showEmpty`.
 */
export function PendingInvitations({ showEmpty = false }: { showEmpty?: boolean }) {
  const { t } = useTranslation()
  const mine = useMyInvitations()
  const respond = useRespondToInvitation()
  const invitations = mine.data?.invitations ?? []

  if (mine.isLoading) {
    return showEmpty ? <Loader2 size={16} className="animate-spin text-gray-400" aria-label={t('common.loading')} /> : null
  }
  if (mine.isError) {
    return (
      <div role="alert" className="alert-error text-sm">
        {t('invitations.loadError', { message: errorDetail(mine.error) })}
      </div>
    )
  }
  if (invitations.length === 0) {
    return showEmpty ? <p className="text-sm text-gray-400">{t('invitations.none')}</p> : null
  }

  return (
    <section aria-labelledby="pending-invitations-title" className="card p-4 space-y-3">
      <h2 id="pending-invitations-title" className="flex items-center gap-2 text-sm font-semibold text-white">
        <Mail size={15} className="text-brand-400" />
        {t('invitations.pendingTitle')}
      </h2>
      <ul className="space-y-2">
        {invitations.map(invitation => (
          <li key={invitation.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-surface-700 bg-surface-800 px-3 py-2">
            <div className="text-sm text-gray-200">
              {t('invitations.summary', {
                company: invitation.company_name || t('auth.companyPlaceholder', { id: invitation.company_id }),
                role: t(`members.roles.${invitation.human_role}`),
                inviter: invitation.invited_by || '—',
              })}
              <div className="text-xs text-gray-500">
                {t('invitations.expires', { date: new Date(invitation.expires_at + 'Z').toLocaleDateString() })}
              </div>
            </div>
            <div className="flex gap-2">
              <button type="button" className="btn btn-primary" disabled={respond.isPending}
                onClick={() => respond.mutate({ kind: 'accept', invitationId: invitation.id })}>
                <Check size={14} />{t('invitations.accept')}
              </button>
              <button type="button" className="btn btn-secondary" disabled={respond.isPending}
                onClick={() => respond.mutate({ kind: 'decline', invitationId: invitation.id })}>
                <X size={14} />{t('invitations.decline')}
              </button>
            </div>
          </li>
        ))}
      </ul>
      {respond.isError && (
        <div role="alert" className="alert-error text-sm">
          {t('invitations.respondError', { message: errorDetail(respond.error) })}
        </div>
      )}
      {respond.data?.status === 'accepted' && (
        <p role="status" className="text-sm text-status-success">{t('invitations.accepted')}</p>
      )}
    </section>
  )
}
