import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyRound, Unlink } from 'lucide-react'
import { ApiError, errorDetail } from '../services/api'
import { useMemberSsoIdentities, useUnlinkMemberSso, type MemberSsoIdentity } from '../services/ssoMemberIdentities'
import { useAuthStore } from '../store/useAuthStore'
import { CountPill, EmptyState, LoadError, LoadingRows, Panel, SavedNote, Spinner, TABLE, TD, TH, TR } from './ui'

const DAY: Intl.DateTimeFormatOptions = { dateStyle: 'medium' }

/** The backend's refusal codes, as the person reads them; anything else as sent. */
function refusal(error: unknown, t: (key: string) => string): string {
  if (error instanceof ApiError && error.detail === 'not_found') return t('ssoMembers.notFound')
  if (error instanceof ApiError && error.detail === 'last_sign_in_method') return t('ssoMembers.lastMethod')
  return errorDetail(error)
}

/**
 * The members' single sign-on identities (`GET/DELETE /v1/companies/{id}/sso/identities`),
 * for an Owner: who is linked through the company's provider, with the
 * provider's address, when it was linked and last used. Unlinking asks first
 * and says what follows: the member's sessions started through single
 * sign-on end at once; they sign in again with another method, or link
 * again. The Owner's own identity is unlinked from Account, not here.
 */
export function SsoMemberIdentities({ companyId }: { companyId: number | null }) {
  const { t } = useTranslation()
  const query = useMemberSsoIdentities(companyId, true)
  const rows = query.data?.identities ?? []

  return (
    <Panel title={<span className="flex items-center gap-1.5"><KeyRound size={13} aria-hidden="true" className="text-gray-400" />{t('ssoMembers.title')}</span>}
      count={rows.length > 0 ? <CountPill count={rows.length} label={t('ssoMembers.count', { count: rows.length })} /> : undefined}>
      {query.isLoading ? (
        <div className="p-3"><LoadingRows rows={1} label={t('ssoMembers.loading')} /></div>
      ) : query.isError ? (
        <div className="p-3"><LoadError onRetry={() => query.refetch()}>{t('ssoMembers.loadError', { message: errorDetail(query.error) })}</LoadError></div>
      ) : rows.length === 0 ? (
        <EmptyState icon={KeyRound} title={t('ssoMembers.empty')} />
      ) : (
        <div className="overflow-x-auto">
          <table className={TABLE}>
            <caption className="sr-only">{t('ssoMembers.title')}</caption>
            <thead>
              <tr>
                <th scope="col" className={TH}>{t('ssoMembers.columns.member')}</th>
                <th scope="col" className={TH}>{t('ssoMembers.columns.providerEmail')}</th>
                <th scope="col" className={TH}>{t('ssoMembers.columns.linked')}</th>
                <th scope="col" className={TH}>{t('ssoMembers.columns.lastLogin')}</th>
                <th scope="col" className={TH}><span className="sr-only">{t('ssoMembers.columns.actions')}</span></th>
              </tr>
            </thead>
            <tbody>{rows.map(row => <IdentityRow key={row.id} identity={row} companyId={companyId} />)}</tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

function IdentityRow({ identity, companyId }: { identity: MemberSsoIdentity; companyId: number | null }) {
  const { t, i18n } = useTranslation()
  const ownId = useAuthStore(state => state.user?.user_id)
  const unlink = useUnlinkMemberSso(companyId)
  const [confirming, setConfirming] = useState(false)
  const day = (value: string | null) => (value ? new Intl.DateTimeFormat(i18n.language, DAY).format(new Date(value)) : t('ssoMembers.never'))
  const self = ownId === identity.user_id

  return (
    <tr className={`${TR} align-top`}>
      <td className={`${TD} text-gray-100`}>{identity.user_email}</td>
      <td className={`${TD} text-gray-300`}>{identity.email ?? '—'}</td>
      <td className={`${TD} tabular-nums text-gray-400`}>{day(identity.linked_at)}</td>
      <td className={`${TD} tabular-nums text-gray-400`}>{day(identity.last_login_at)}</td>
      <td className={`${TD} text-right`}>
        {self ? (
          <span className="text-meta">{t('ssoMembers.yours')}</span>
        ) : unlink.isSuccess ? (
          <SavedNote>{t('ssoMembers.unlinked', { count: unlink.data.sessions_ended })}</SavedNote>
        ) : confirming ? (
          <span role="group" aria-label={t('ssoMembers.confirmLabel', { email: identity.user_email })} className="inline-flex max-w-xs flex-wrap items-center justify-end gap-1">
            <span className="text-left text-xs text-amber-300">{t('ssoMembers.confirm', { email: identity.user_email })}</span>
            <button type="button" className="btn-tool border border-red-700/60 text-red-300" disabled={unlink.isPending}
              onClick={() => unlink.mutate(identity.id, { onSettled: () => setConfirming(false) })}>
              {unlink.isPending && <Spinner size={13} />}{t('ssoMembers.unlinkConfirm')}
            </button>
            <button type="button" className="btn-tool" onClick={() => setConfirming(false)}>{t('common.cancel')}</button>
          </span>
        ) : (
          <button type="button" className="btn-tool hover:text-red-300" onClick={() => setConfirming(true)}
            aria-label={t('ssoMembers.unlinkNamed', { email: identity.user_email })}>
            <Unlink size={13} aria-hidden="true" />{t('ssoMembers.unlink')}
          </button>
        )}
        {unlink.isError && <span role="alert" className="mt-1 block text-xs text-red-300">{refusal(unlink.error, t)}</span>}
      </td>
    </tr>
  )
}
