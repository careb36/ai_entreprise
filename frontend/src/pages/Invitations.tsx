import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { Mail } from 'lucide-react'
import { PendingInvitations } from '../components/PendingInvitations'
import { useRespondToInvitation } from '../hooks/useInvitations'
import { ApiError, errorDetail } from '../services/api'

/**
 * The signed-in person's invitations. A link from an inviter carries
 * `?token=`: it is accepted once, and only for the invited address.
 */
export default function Invitations() {
  const { t } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const token = searchParams.get('token')
  const byToken = useRespondToInvitation()
  const sent = useRef<string | null>(null)

  useEffect(() => {
    if (!token || sent.current === token) return
    sent.current = token
    byToken.mutate({ kind: 'token', token })
    // The token leaves the address bar once used.
    setSearchParams({}, { replace: true })
  }, [token, byToken, setSearchParams])

  const status = byToken.error instanceof ApiError ? byToken.error.status : null
  const tokenError = status === 404
    ? t('invitations.tokenNotFound')
    : status === 410
      ? t('invitations.tokenExpired')
      : t('invitations.respondError', { message: errorDetail(byToken.error) })

  return (
    <div className="page-container max-w-2xl space-y-4">
      <div className="page-header">
        <div className="flex items-center gap-2">
          <Mail className="text-brand-400" size={22} />
          <h1 className="page-title">{t('invitations.title')}</h1>
        </div>
      </div>
      {byToken.isSuccess && (
        <p role="status" className="card p-4 text-sm text-status-success">{t('invitations.accepted')}</p>
      )}
      {byToken.isError && <div role="alert" className="alert-error text-sm">{tokenError}</div>}
      <PendingInvitations showEmpty />
    </div>
  )
}
