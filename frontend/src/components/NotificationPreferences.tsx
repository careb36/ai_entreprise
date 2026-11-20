import { useTranslation } from 'react-i18next'
import { useNotificationPreferences, useSetNotificationPreference } from '../hooks/useNotifications'
import { errorDetail, type NotificationPreference } from '../services/api'
import { Panel } from './ui'

/**
 * The person's choice per kind of notification: shown in the app, and copied
 * by email when the deployment can send it. A security notice is always
 * shown in the app (its box is checked and disabled, and the reason is
 * written next to it); an email needs the app copy. Each row is a fieldset
 * whose legend names the kind, so the two checkboxes are announced with it
 * (WCAG 2.2: 1.3.1, 3.3.2, 4.1.2).
 */
export function NotificationPreferences() {
  const { t } = useTranslation()
  const prefs = useNotificationPreferences()
  const save = useSetNotificationPreference()
  if (prefs.isLoading) return null
  if (prefs.isError) return <div role="alert" className="alert-error text-sm">{errorDetail(prefs.error)}</div>
  const emailAvailable = Boolean(prefs.data?.email_available)

  const change = (pref: NotificationPreference, next: Partial<Pick<NotificationPreference, 'in_app' | 'email'>>) => {
    const inApp = pref.in_app_locked ? true : next.in_app ?? pref.in_app
    save.mutate({ kind: pref.kind, in_app: inApp, email: inApp ? next.email ?? pref.email : false })
  }

  return (
    <Panel title={t('personalNotifications.preferencesTitle')}>
      <div className="space-y-3 p-3">
        <p className="text-xs text-gray-400">
          {emailAvailable ? t('personalNotifications.emailAvailable') : t('personalNotifications.emailUnavailable')}
        </p>
        {(prefs.data?.preferences ?? []).map(pref => {
          const label = t(`personalNotifications.kinds.${pref.kind}`, { defaultValue: t('personalNotifications.kinds.other') })
          return (
            <fieldset key={pref.kind} className="rounded border border-surface-700 p-3">
              <legend className="px-1 text-sm text-gray-100">{label}</legend>
              <div className="flex flex-wrap gap-4 text-sm text-gray-300">
                <label className="flex items-center gap-2">
                  <input type="checkbox" className="h-4 w-4 rounded accent-brand-500" checked={pref.in_app}
                    disabled={pref.in_app_locked || save.isPending}
                    onChange={event => change(pref, { in_app: event.target.checked })} />
                  {t('personalNotifications.inApp')}
                </label>
                <label className="flex items-center gap-2">
                  <input type="checkbox" className="h-4 w-4 rounded accent-brand-500" checked={pref.email}
                    disabled={!emailAvailable || !pref.in_app || save.isPending}
                    onChange={event => change(pref, { email: event.target.checked })} />
                  {t('personalNotifications.email')}
                </label>
              </div>
              {pref.in_app_locked && (
                <p className="mt-2 text-xs text-gray-400">{t('personalNotifications.securityLocked')}</p>
              )}
            </fieldset>
          )
        })}
        {save.isError && (
          <div role="alert" className="alert-error text-sm">
            {t(`personalNotifications.errors.${errorDetail(save.error)}`, { defaultValue: errorDetail(save.error) })}
          </div>
        )}
      </div>
    </Panel>
  )
}
