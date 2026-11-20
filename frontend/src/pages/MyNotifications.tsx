import { useTranslation } from 'react-i18next'
import { NotificationList } from '../components/NotificationList'
import { NotificationPreferences } from '../components/NotificationPreferences'
import { PageHeader } from '../components/ui'

/** The signed-in person's own notifications and their preferences (issue #683). */
export default function MyNotifications() {
  const { t } = useTranslation()
  return (
    <div className="page-container max-w-3xl space-y-4">
      <PageHeader title={t('personalNotifications.title')} subtitle={t('personalNotifications.subtitle')} />
      <NotificationList />
      <NotificationPreferences />
    </div>
  )
}
