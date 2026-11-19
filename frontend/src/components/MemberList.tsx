import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { UserMinus, Users } from 'lucide-react'
import { useAccessReview, useRemoveMember } from '../hooks/useDuties'
import { errorDetail, type AccessReviewMember } from '../services/api'
import { useAuthStore } from '../store/useAuthStore'
import { CountPill, EmptyState, LoadError, LoadingRows, Panel, Spinner, TABLE, TD, TH, TR } from './ui'

const DAY: Intl.DateTimeFormatOptions = { dateStyle: 'medium' }
const formatDay = (value: string, locale: string) => new Intl.DateTimeFormat(locale, DAY).format(new Date(value))

/**
 * The company's members, for its Owners (`DELETE /v1/company/{id}/members/{user_id}`,
 * Owner): each with their own and effective role and last activity, read
 * from the access review's listing, and a way to remove one now, after a
 * confirmation that says what happens (their sessions end everywhere, their
 * teams are left). The backend keeps the last Owner and audits the removal.
 * An Owner's own row has no removal: leaving is not done from here.
 */
export function MemberList({ companyId }: { companyId: number | null }) {
  const { t } = useTranslation()
  const review = useAccessReview(companyId, true)
  const members = review.data?.members ?? []

  return (
    <Panel title={t('members.listTitle')}
      count={members.length > 0 ? <CountPill count={members.length} label={t('members.listCount', { count: members.length })} /> : undefined}>
      {review.isLoading ? (
        <div className="p-3"><LoadingRows rows={1} label={t('members.listLoading')} /></div>
      ) : review.isError ? (
        <div className="p-3"><LoadError onRetry={() => review.refetch()}>{t('members.listError', { message: errorDetail(review.error) })}</LoadError></div>
      ) : members.length === 0 ? (
        <EmptyState icon={Users} title={t('members.listEmpty')} />
      ) : (
        <div className="overflow-x-auto">
          <table className={TABLE}>
            <caption className="sr-only">{t('members.listTitle')}</caption>
            <thead>
              <tr>
                <th scope="col" className={TH}>{t('members.columns.member')}</th>
                <th scope="col" className={TH}>{t('members.columns.role')}</th>
                <th scope="col" className={TH}>{t('members.columns.lastActivity')}</th>
                <th scope="col" className={TH}><span className="sr-only">{t('members.columns.actions')}</span></th>
              </tr>
            </thead>
            <tbody>
              {members.map(member => <MemberRow key={member.id} member={member} companyId={companyId} />)}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

function MemberRow({ member, companyId }: { member: AccessReviewMember; companyId: number | null }) {
  const { t, i18n } = useTranslation()
  const ownId = useAuthStore(state => state.user?.user_id)
  const remove = useRemoveMember(companyId)
  const [confirming, setConfirming] = useState(false)
  const self = ownId === member.id
  const role = member.effective_role && member.effective_role !== member.own_role
    ? t('members.roleRaised', { own: member.own_role, effective: member.effective_role })
    : member.own_role

  return (
    <tr className={`${TR} align-top`}>
      <td className={TD}>
        <div className="text-[13px] text-gray-100">{member.label}</div>
        {member.label !== member.email && <div className="text-meta">{member.email}</div>}
      </td>
      <td className={`${TD} text-gray-300`}>{role}</td>
      <td className={`${TD} tabular-nums text-gray-400`}>
        {member.last_activity ? formatDay(member.last_activity, i18n.language) : t('members.neverActive')}
      </td>
      <td className={`${TD} text-right`}>
        {self ? (
          <span className="text-meta">{t('members.you')}</span>
        ) : confirming ? (
          <span role="group" aria-label={t('members.confirmRemoveLabel', { name: member.label })} className="inline-flex flex-wrap items-center justify-end gap-1">
            <span className="text-xs text-amber-300">{t('members.confirmRemove', { name: member.label })}</span>
            <button type="button" className="btn-tool border border-red-700/60 text-red-300" disabled={remove.isPending}
              onClick={() => remove.mutate(member.id, { onSettled: () => setConfirming(false) })}>
              {remove.isPending && <Spinner size={13} />}{t('members.removeConfirm')}
            </button>
            <button type="button" className="btn-tool" onClick={() => setConfirming(false)}>{t('common.cancel')}</button>
          </span>
        ) : (
          <button type="button" className="btn-tool hover:text-red-300" onClick={() => setConfirming(true)}
            aria-label={t('members.removeNamed', { name: member.label })}>
            <UserMinus size={13} aria-hidden="true" />{t('members.remove')}
          </button>
        )}
        {remove.isError && <span role="alert" className="mt-1 block text-xs text-red-300">{errorDetail(remove.error)}</span>}
      </td>
    </tr>
  )
}
