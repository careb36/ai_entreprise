import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { BellOff, Check, ShieldAlert, Undo2 } from 'lucide-react'
import {
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotifications,
  useSnoozeNotification,
} from '../hooks/useNotifications'
import { errorDetail, type NotificationState, type PersonalNotification } from '../services/api'
import { useAuthStore } from '../store/useAuthStore'
import { useAppStore } from '../store/useAppStore'
import { companyLabel } from '../lib/companyLabel'
import { EmptyState, Panel } from './ui'
import { SegmentedFilter } from './ui/SegmentedFilter'

const HOUR = 60 * 60 * 1000
/** Snooze choices, in hours; a security notice is snoozed for a day at most. */
const SNOOZE_HOURS = [1, 4, 24, 72, 168]
const SECURITY_MAX_HOURS = 24

/** Where a notice's subject is read, in the receiver's own pages. */
export function notificationLink(notice: PersonalNotification): string {
  const sprintId = notice.context.sprint_id
  switch (notice.kind) {
    case 'sso_break_glass_exclusion':
    case 'sso_break_glass_lifted':
      return '/company/teams'
    case 'sso_domain_lapsed':
      return '/company/sso'
    case 'review_requested':
      return sprintId ? `/sprints/${sprintId}/tasks/${notice.subject.id}` : '/governance'
    case 'integration_waiting':
      return sprintId ? `/sprints/${sprintId}` : '/governance'
    default:
      return '/governance'
  }
}

function when(iso: string | null): string {
  return iso ? new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`).toLocaleString() : ''
}

/**
 * The person's own notifications (issue #683): a filter (unread, snoozed,
 * all), each notice with what it is, when, a link to its subject, mark read
 * or unread, and snooze. The list is a `<ul>` with one heading level; every
 * control is a labelled native button or select (WCAG 2.2 AA: 1.3.1, 2.1.1,
 * 2.4.6, 4.1.2), and a security notice says so in words, not colour alone.
 */
export function NotificationList() {
  const { t } = useTranslation()
  const [state, setState] = useState<NotificationState>('unread')
  const list = useNotifications(state)
  const markRead = useMarkNotificationRead()
  const markAll = useMarkAllNotificationsRead()
  const snooze = useSnoozeNotification()
  const memberships = useAuthStore(s => s.memberships)
  const companyId = useAppStore(s => s.companyId)
  const setCompany = useAppStore(s => s.setCompany)
  const notices = list.data?.notifications ?? []
  const unread = list.data?.unread ?? 0
  const failure = markRead.error ?? markAll.error ?? snooze.error

  const kindLabel = (kind: string) =>
    t(`personalNotifications.kinds.${kind}`, { defaultValue: t('personalNotifications.kinds.other') })

  return (
    <Panel title={t('personalNotifications.listTitle')} count={unread}
      actions={unread > 0 && (
        <button type="button" className="btn-tool border border-surface-700" disabled={markAll.isPending}
          onClick={() => markAll.mutate()}>
          <Check size={13} aria-hidden="true" />{t('personalNotifications.markAllRead')}
        </button>
      )}>
      <div className="space-y-3 p-3">
        <SegmentedFilter<NotificationState> label={t('personalNotifications.filterLabel')} value={state} onChange={setState}
          options={[
            { value: 'unread', label: t('personalNotifications.states.unread'), count: unread },
            { value: 'snoozed', label: t('personalNotifications.states.snoozed') },
            { value: 'all', label: t('personalNotifications.states.all') },
          ]} />
        {failure && <div role="alert" className="alert-error text-sm">
          {t(`personalNotifications.errors.${errorDetail(failure)}`, { defaultValue: errorDetail(failure) })}
        </div>}
        {list.isError && <div role="alert" className="alert-error text-sm">{errorDetail(list.error)}</div>}
        {list.isSuccess && notices.length === 0 && (
          <EmptyState icon={BellOff} role="status" dashed title={t(`personalNotifications.empty.${state}`)} />
        )}
        {notices.length > 0 && (
          <ul className="space-y-2" aria-label={t('personalNotifications.listTitle')}>
            {notices.map(notice => {
              const otherCompany = notice.company_id !== companyId && memberships.length > 1
              const maxHours = notice.security ? SECURITY_MAX_HOURS : Infinity
              return (
                <li key={notice.id}
                  className={`rounded border p-3 ${notice.read_at ? 'border-surface-700' : 'border-brand-500/60 bg-surface-800/60'}`}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0 space-y-1 text-sm">
                      <p className="flex flex-wrap items-center gap-2 text-gray-100">
                        {notice.security && (
                          <span className="inline-flex items-center gap-1 rounded bg-red-900/50 px-1.5 py-0.5 text-xs font-semibold text-red-200">
                            <ShieldAlert size={12} aria-hidden="true" />{t('personalNotifications.security')}
                          </span>
                        )}
                        <span>{kindLabel(notice.kind)}</span>
                        {!notice.read_at && <span className="text-xs text-brand-300">{t('personalNotifications.unread')}</span>}
                      </p>
                      <p className="text-xs text-gray-400">
                        <time dateTime={notice.created_at ?? undefined}>{when(notice.created_at)}</time>
                        {otherCompany && ` · ${t('personalNotifications.company', { id: notice.company_id })}`}
                        {notice.snoozed_until && ` · ${t('personalNotifications.snoozedUntil', { date: when(notice.snoozed_until) })}`}
                      </p>
                      <Link to={notificationLink(notice)} className="text-xs text-brand-300 underline"
                        onClick={() => { if (notice.company_id !== companyId) setCompany(notice.company_id, companyLabel(memberships, notice.company_id, t)) }}>
                        {t('personalNotifications.open')}
                      </Link>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <button type="button" className="btn-tool border border-surface-700" disabled={markRead.isPending}
                        onClick={() => markRead.mutate({ id: notice.id, read: !notice.read_at })}>
                        {notice.read_at
                          ? <><Undo2 size={13} aria-hidden="true" />{t('personalNotifications.markUnread')}</>
                          : <><Check size={13} aria-hidden="true" />{t('personalNotifications.markRead')}</>}
                      </button>
                      {!notice.read_at && (notice.snoozed_until ? (
                        <button type="button" className="btn-tool border border-surface-700" disabled={snooze.isPending}
                          onClick={() => snooze.mutate({ id: notice.id, until: null })}>
                          {t('personalNotifications.unsnooze')}
                        </button>
                      ) : (
                        <label className="flex items-center gap-1 text-xs text-gray-300">
                          <span>{t('personalNotifications.snooze')}</span>
                          <select className="select control-sm w-auto pr-7" value="" disabled={snooze.isPending}
                            onChange={event => {
                              const hours = Number(event.target.value)
                              if (hours > 0) snooze.mutate({ id: notice.id, until: new Date(Date.now() + hours * HOUR).toISOString() })
                            }}>
                            <option value="">{t('personalNotifications.snoozeChoose')}</option>
                            {SNOOZE_HOURS.filter(hours => hours <= maxHours).map(hours => (
                              <option key={hours} value={hours}>{t('personalNotifications.snoozeFor', { count: hours })}</option>
                            ))}
                          </select>
                        </label>
                      ))}
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </Panel>
  )
}
