import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BellRing, Send, Trash2 } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import { MIN_SECRET, usePingWebhook, useWebhook, useWebhookChange, type WebhookSetting } from '../services/webhook'
import { useAppStore } from '../store/useAppStore'
import { FormActions, LoadError, LoadingRows, PageHeader, Panel, SavedNote, Spinner } from '../components/ui'

/** Only an https URL without credentials, as the backend requires (it checks again, and resolves the host). */
export function urlProblem(value: string): 'scheme' | 'credentials' | 'invalid' | null {
  let url: URL
  try {
    url = new URL(value)
  } catch {
    return 'invalid'
  }
  if (url.protocol !== 'https:') return 'scheme'
  if (url.username || url.password) return 'credentials'
  return null
}

/**
 * Notifications (`/company/notifications`): the company's notification
 * webhook. Every member sees where it sends, which events and how the last
 * delivery went; a Manager creates or replaces it, sends a test ping and
 * deletes it after a confirmation. The signing secret is written, never read
 * back: the field is a password field cleared after each save.
 */
export default function Notifications() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const query = useWebhook(companyId)

  return (
    <div className="page-container max-w-3xl space-y-4">
      <PageHeader title={t('webhook.title')} subtitle={t('webhook.subtitle')} />
      {query.isLoading ? <LoadingRows rows={1} label={t('webhook.loading')} />
        : query.isError ? <LoadError onRetry={() => query.refetch()}>{t('webhook.loadError', { message: errorDetail(query.error) })}</LoadError>
          : query.data && (
            <>
              <Status setting={query.data} canManage={canManage} />
              {canManage && <WebhookForm key={query.data.webhook?.url ?? 'new'} setting={query.data} />}
            </>
          )}
    </div>
  )
}

function Status({ setting, canManage }: { setting: WebhookSetting; canManage: boolean }) {
  const { t, i18n } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const ping = usePingWebhook(companyId)
  const change = useWebhookChange(companyId)
  const [confirming, setConfirming] = useState(false)
  const hook = setting.webhook

  return (
    <Panel title={<span className="flex items-center gap-1.5"><BellRing size={13} aria-hidden="true" className="text-gray-400" />{t('webhook.statusTitle')}</span>}>
      <div className="space-y-2 p-3 text-xs">
        {!hook ? (
          <p className="text-gray-400">{canManage ? t('webhook.noneManager') : t('webhook.none')}</p>
        ) : (
          <>
            <p className="break-all font-mono text-gray-100">{hook.url}</p>
            <p className="text-gray-300">
              {hook.enabled ? t('webhook.on') : t('webhook.off')}
              {' · '}{hook.events.length > 0 ? t('webhook.eventsList', { events: hook.events.map(e => t(`webhook.events.${e}`, { defaultValue: e })).join(', ') }) : t('webhook.noEvents')}
            </p>
            <p className="text-gray-400">
              {hook.last_delivery_at
                ? t('webhook.lastDelivery', {
                  when: new Intl.DateTimeFormat(i18n.language, DATE_TIME).format(new Date(hook.last_delivery_at)),
                  status: hook.last_status ?? '—',
                })
                : t('webhook.noDelivery')}
            </p>
            {hook.last_error && <p role="note" className="text-amber-300">{t('webhook.lastError', { error: hook.last_error })}</p>}
            {canManage && (
              <FormActions>
                <button type="button" className="btn-tool border border-surface-700" disabled={ping.isPending} onClick={() => ping.mutate()}>
                  {ping.isPending ? <Spinner size={13} /> : <Send size={13} aria-hidden="true" />}{t('webhook.ping')}
                </button>
                {ping.isSuccess && <SavedNote>{t('webhook.pinged')}</SavedNote>}
                {confirming ? (
                  <span role="group" aria-label={t('webhook.confirmLabel')} className="inline-flex flex-wrap items-center gap-1">
                    <span className="text-amber-300">{t('webhook.confirmDelete')}</span>
                    <button type="button" className="btn-tool border border-red-700/60 text-red-300" disabled={change.isPending}
                      onClick={() => change.mutate({ kind: 'remove' }, { onSettled: () => setConfirming(false) })}>{t('webhook.deleteConfirm')}</button>
                    <button type="button" className="btn-tool" onClick={() => setConfirming(false)}>{t('common.cancel')}</button>
                  </span>
                ) : (
                  <button type="button" className="btn-tool hover:text-red-300" onClick={() => setConfirming(true)}>
                    <Trash2 size={13} aria-hidden="true" />{t('webhook.delete')}
                  </button>
                )}
                {(ping.isError || change.isError) && <span role="alert" className="text-red-300">{errorDetail(ping.error ?? change.error)}</span>}
              </FormActions>
            )}
          </>
        )}
      </div>
    </Panel>
  )
}

const DATE_TIME: Intl.DateTimeFormatOptions = { dateStyle: 'medium', timeStyle: 'short' }

function WebhookForm({ setting }: { setting: WebhookSetting }) {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const change = useWebhookChange(companyId)
  const ids = { url: useId(), secret: useId(), urlHint: useId(), secretHint: useId(), enabled: useId() }
  const hook = setting.webhook
  const [url, setUrl] = useState(hook?.url ?? '')
  const [secret, setSecret] = useState('')
  const [events, setEvents] = useState<string[]>(hook?.events ?? setting.events)
  const [enabled, setEnabled] = useState(hook?.enabled ?? true)
  const [saved, setSaved] = useState(false)
  const problem = url.trim() ? urlProblem(url.trim()) : null
  const secretNeeded = !hook?.has_secret
  const secretShort = secret !== '' && secret.trim().length < MIN_SECRET
  const ready = url.trim() && !problem && !secretShort && (!secretNeeded || secret.trim())

  function toggle(event: string) {
    setEvents(current => current.includes(event) ? current.filter(e => e !== event) : [...current, event])
  }

  return (
    <Panel title={hook ? t('webhook.editTitle') : t('webhook.createTitle')}>
      <form className="space-y-3 p-3" aria-label={hook ? t('webhook.editTitle') : t('webhook.createTitle')}
        onSubmit={event => {
          event.preventDefault()
          if (!ready) return
          setSaved(false)
          change.mutate(
            { kind: 'save', input: { url: url.trim(), events, enabled, ...(secret.trim() ? { secret: secret.trim() } : {}) } },
            { onSuccess: () => { setSecret(''); setSaved(true) } },
          )
        }}>
        <div>
          <label htmlFor={ids.url} className="input-label">{t('webhook.url')}</label>
          <input id={ids.url} type="url" required value={url} onChange={event => setUrl(event.target.value)} placeholder={t('webhook.urlPlaceholder')}
            aria-invalid={Boolean(problem)} aria-describedby={ids.urlHint} className="input w-full font-mono text-xs" autoComplete="off" />
          <p id={ids.urlHint} className={`text-meta ${problem ? 'text-red-300' : ''}`}>{problem ? t(`webhook.urlProblem.${problem}`) : t('webhook.urlHint')}</p>
        </div>
        <div>
          <label htmlFor={ids.secret} className="input-label">{secretNeeded ? t('webhook.secretRequired') : t('webhook.secretReplace')}</label>
          <input id={ids.secret} type="password" value={secret} required={secretNeeded} minLength={MIN_SECRET} autoComplete="new-password"
            onChange={event => setSecret(event.target.value)} aria-invalid={secretShort} aria-describedby={ids.secretHint} className="input w-full" />
          <p id={ids.secretHint} className={`text-meta ${secretShort ? 'text-red-300' : ''}`}>{t('webhook.secretHint', { count: MIN_SECRET })}</p>
        </div>
        <fieldset className="space-y-1">
          <legend className="input-label">{t('webhook.eventsLabel')}</legend>
          {setting.events.map(event => (
            <label key={event} className="flex items-center gap-2 text-xs text-gray-200">
              <input type="checkbox" className="accent-brand-500" checked={events.includes(event)} onChange={() => toggle(event)} />
              {t(`webhook.events.${event}`, { defaultValue: event })}
            </label>
          ))}
          <p className="text-meta">{t('webhook.eventsHint')}</p>
        </fieldset>
        <label htmlFor={ids.enabled} className="flex items-center gap-2 text-xs text-gray-200">
          <input id={ids.enabled} type="checkbox" className="accent-brand-500" checked={enabled} onChange={event => setEnabled(event.target.checked)} />
          {t('webhook.enabled')}
        </label>
        <FormActions>
          <button type="submit" className="btn-tool border border-surface-700" disabled={change.isPending || !ready}>
            {change.isPending && <Spinner size={13} />}{t('common.save')}
          </button>
          {saved && <SavedNote>{t('webhook.saved')}</SavedNote>}
          {change.isError && <span role="alert" className="text-xs text-red-300">{errorDetail(change.error)}</span>}
        </FormActions>
      </form>
    </Panel>
  )
}
