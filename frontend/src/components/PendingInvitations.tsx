import { useTranslation } from 'react-i18next'
import { Check, Mail, X } from 'lucide-react'
import { useMyInvitations, useRespondToInvitation } from '../hooks/useInvitations'
import { errorDetail } from '../services/api'
import { CountPill, EmptyState, Panel } from './ui'

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
    return showEmpty ? (
      <div role="status" aria-label={t('common.loading')} className="skeleton h-14 rounded-xl" />
    ) : null
  }
  if (mine.isError) {
    return (
      <div role="alert" className="alert-error text-sm">
        {t('invitations.loadError', { message: errorDetail(mine.error) })}
      </div>
    )
  }
  if (invitations.length === 0) {
    return showEmpty ? <EmptyState icon={Mail} title={t('invitations.none')} dashed /> : null
  }

  return (
    <Panel title={t('invitations.pendingTitle')}
      count={<CountPill count={invitations.length} label={t('invitations.count', { count: invitations.length })} />}>
      <ul className="data-list">
        {invitations.map(invitation => (
          <li key={invitation.id} className="data-row flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[13px] text-gray-100">
                {t('invitations.summary', {
                  company: invitation.company_name || t('auth.companyPlaceholder', { id: invitation.company_id }),
                  role: t(`members.roles.${invitation.human_role}`),
                  inviter: invitation.invited_by || '—',
                })}
              </p>
              <p className="text-meta mt-0.5 tabular-nums">
                {t('invitations.expires', { date: new Date(invitation.expires_at + 'Z').toLocaleDateString() })}
              </p>
            </div>
            <div className="flex gap-2">
              <button type="button" className="btn-primary btn-sm" disabled={respond.isPending}
                onClick={() => respond.mutate({ kind: 'accept', invitationId: invitation.id })}>
                <Check size={13} aria-hidden="true" />{t('invitations.accept')}
              </button>
              <button type="button" className="btn-tool border border-surface-700" disabled={respond.isPending}
                onClick={() => respond.mutate({ kind: 'decline', invitationId: invitation.id })}>
                <X size={13} aria-hidden="true" />{t('invitations.decline')}
              </button>
            </div>
          </li>
        ))}
      </ul>
      {(respond.isError || respond.data?.status === 'accepted') && (
        <div className="border-t border-surface-700 p-3">
          {respond.isError && (
            <div role="alert" className="alert-error text-sm">
              {t('invitations.respondError', { message: errorDetail(respond.error) })}
            </div>
          )}
          {respond.data?.status === 'accepted' && (
            <p role="status" className="text-sm text-status-success">{t('invitations.accepted')}</p>
          )}
        </div>
      )}
    </Panel>
  )
}
