import { useTranslation } from 'react-i18next'
import { useServiceAccounts } from '../hooks/useDuties'
import { errorDetail, type ServiceAccount } from '../services/api'
import type { Translate } from '../i18n/translate'
import { Panel, TABLE, TD, TH, TR } from './ui'

/**
 * The credentials that act without a person (issue #543): API tokens, the
 * webhook, chat connections, the SSO client, model providers and GitHub
 * webhooks, with owner, scope and age; and the deployment's NATS method and
 * the LLM proxy token's presence. Names only, never a value.
 */
export default function ServiceAccountsPanel({ companyId }: { companyId: number | null }) {
  const { t } = useTranslation()
  const accounts = useServiceAccounts(companyId, true)

  if (accounts.isError) return <div role="alert" className="alert-error text-sm">{errorDetail(accounts.error)}</div>
  if (!accounts.data) return null
  return (
    <Panel title={t('duties.accounts.title')}>
      <p className="px-3 pt-3 text-xs text-gray-400">{t('duties.accounts.hint')}</p>
      {accounts.data.company.length === 0
        ? <p className="px-3 py-3 text-xs text-gray-400">{t('duties.accounts.none')}</p>
        : (
          <div className="mt-2 overflow-x-auto">
            <table className={TABLE} aria-label={t('duties.accounts.company')}>
              <thead>
                <tr>
                  <th className={TH}>{t('duties.accounts.kind')}</th>
                  <th className={TH}>{t('duties.accounts.name')}</th>
                  <th className={TH}>{t('duties.accounts.owner')}</th>
                  <th className={TH}>{t('duties.accounts.scope')}</th>
                  <th className={TH}>{t('duties.accounts.age')}</th>
                  <th className={TH}>{t('duties.accounts.status')}</th>
                </tr>
              </thead>
              <tbody>
                {accounts.data.company.map((row, i) => (
                  <tr key={`${row.kind}-${i}`} className={TR}>
                    <td className={TD}>{t(`duties.accounts.kinds.${row.kind}`, { defaultValue: row.kind })}</td>
                    <td className={`${TD} font-mono`}>{row.name ?? '—'}</td>
                    <td className={TD}>{row.owner ?? '—'}</td>
                    <td className={TD}>{row.scope}</td>
                    <td className={`${TD} tabular-nums`}>{row.age_days == null ? '—' : t('duties.accounts.days', { count: row.age_days })}</td>
                    <td className={TD}>{row.status ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      <ul className="space-y-1 border-t border-surface-700 px-3 py-2 text-xs text-gray-400" aria-label={t('duties.accounts.deployment')}>
        {accounts.data.deployment.map(row => <li key={row.kind}>{deploymentLine(row, t)}</li>)}
      </ul>
    </Panel>
  )
}

function deploymentLine(row: ServiceAccount, t: Translate): string {
  if (row.kind === 'llm_proxy_token') {
    return t(row.present ? 'duties.accounts.proxyPresent' : 'duties.accounts.proxyMissing')
  }
  return t(`duties.accounts.kinds.${row.kind}`, { defaultValue: row.kind }) + ': ' + (row.name ?? '—') + ' · ' + (row.method ?? '—')
}
