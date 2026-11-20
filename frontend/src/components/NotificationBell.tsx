import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Bell } from 'lucide-react'
import { useNotificationCount } from '../hooks/useNotifications'

/**
 * The bell in the top bar: a link to the person's own notifications, with
 * the unread count in its accessible name (WCAG 2.2 SC 1.1.1, 4.1.2) and a
 * badge drawn beside it. The count is never shown by colour alone.
 */
export function NotificationBell({ enabled }: { enabled: boolean }) {
  const { t } = useTranslation()
  const count = useNotificationCount(enabled)
  if (!enabled || count.isError) return null
  const unread = count.data?.unread ?? 0
  const label = unread > 0
    ? t('personalNotifications.bellUnread', { count: unread })
    : t('personalNotifications.bell')
  return (
    <Link to="/notifications" className="btn-ghost btn-icon relative h-8 w-8" aria-label={label} title={label}>
      <Bell size={16} aria-hidden="true" />
      {unread > 0 && (
        <span aria-hidden="true"
          className="absolute -right-1 -top-1 min-w-4 rounded-full bg-red-600 px-1 text-center text-[10px] font-bold leading-4 text-white tabular-nums">
          {unread > 99 ? '99+' : unread}
        </span>
      )}
    </Link>
  )
}
