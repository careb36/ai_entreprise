import { useTranslation } from 'react-i18next'
import { CircleAlert, CircleCheck, Clock, RefreshCw } from 'lucide-react'
import { useCheckSsoDomain } from '../hooks/useSso'
import { errorDetail, type SsoDomain } from '../services/api'
import { Panel, StatusBadge, type StatusTone } from './ui'

const STATUS: Record<SsoDomain['status'], { tone: StatusTone; icon: typeof CircleCheck }> = {
  verified: { tone: 'success', icon: CircleCheck },
  grace: { tone: 'warning', icon: Clock },
  unverified: { tone: 'danger', icon: CircleAlert },
}

/**
 * Each allowed domain's DNS verification (docs/design/SSO.md): the TXT record
 * the Owner publishes, its status and a check now. Single sign-on creates or
 * links accounts only in a verified domain.
 */
export function SsoDomains({ companyId, domains }: { companyId: number | null; domains: SsoDomain[] }) {
  const { t } = useTranslation()
  const check = useCheckSsoDomain(companyId)
  const last = check.data

  return (
    <div className="mt-4">
      <Panel title={t('sso.domainCheck.title')}>
        <div className="space-y-3 p-3">
          <p className="text-xs text-gray-400">{t('sso.domainCheck.intro')}</p>
          <ul className="space-y-3">
            {domains.map(domain => {
              const status = STATUS[domain.status]
              const pending = check.isPending && check.variables === domain.domain
              return (
                <li key={domain.domain} className="rounded border border-surface-700 p-3" aria-label={domain.domain}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm text-gray-100">{domain.domain}</span>
                      <StatusBadge icon={status.icon} tone={status.tone} label={t(`sso.domainCheck.status.${domain.status}`)} />
                    </div>
                    <button type="button" className="btn-tool border border-surface-700" disabled={check.isPending}
                      onClick={() => check.mutate(domain.domain)}>
                      <RefreshCw size={13} aria-hidden="true" className={pending ? 'animate-spin' : undefined} />
                      {t('sso.domainCheck.check')}
                    </button>
                  </div>
                  <dl className="mt-2 grid gap-1 text-xs text-gray-400 sm:grid-cols-[7rem_1fr]">
                    <dt>{t('sso.domainCheck.recordName')}</dt>
                    <dd><code className="break-all font-mono text-gray-200">{domain.record_name}</code></dd>
                    <dt>{t('sso.domainCheck.recordValue')}</dt>
                    <dd><code className="break-all font-mono text-gray-200">{domain.record_value}</code></dd>
                    {domain.status === 'grace' && domain.grace_until && (
                      <>
                        <dt>{t('sso.domainCheck.graceUntil')}</dt>
                        <dd>{new Date(domain.grace_until).toLocaleDateString()}</dd>
                      </>
                    )}
                    {domain.last_checked_at && (
                      <>
                        <dt>{t('sso.domainCheck.lastChecked')}</dt>
                        <dd>
                          {new Date(domain.last_checked_at).toLocaleString()}
                          {domain.last_outcome && ` · ${t(`sso.domainCheck.outcome.${domain.last_outcome}`)}`}
                        </dd>
                      </>
                    )}
                  </dl>
                </li>
              )
            })}
          </ul>
          {check.isError && <div role="alert" className="alert-error text-sm">{errorDetail(check.error)}</div>}
          {last && (
            <div role="status" className={`${last.outcome === 'verified' ? 'alert-success' : 'alert-error'} text-sm`}>
              <p>
                {t(`sso.domainCheck.outcome.${last.outcome}`)}
                {last.answer.length > 0 && ` ${t('sso.domainCheck.seen', { values: last.answer.join(' | ') })}`}
              </p>
            </div>
          )}
        </div>
      </Panel>
    </div>
  )
}
