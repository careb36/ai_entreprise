import { useTranslation } from 'react-i18next'
import { useServiceAccounts } from '../hooks/useDuties'
import { errorDetail, type ServiceAccount } from '../services/api'

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
    <section className="card p-4 space-y-3" aria-label={t('duties.accounts.title')}>
      <h2 className="text-base font-semibold text-gray-100">{t('duties.accounts.title')}</h2>
      <p className="text-xs text-gray-400">{t('duties.accounts.hint')}</p>
      {accounts.data.company.length === 0
        ? <p className="text-sm text-gray-400">{t('duties.accounts.none')}</p>
        : (
          <table className="w-full text-sm" aria-label={t('duties.accounts.company')}>
            <thead className="text-left text-xs text-gray-400">
              <tr>
                <th className="py-1">{t('duties.accounts.kind')}</th>
                <th>{t('duties.accounts.name')}</th>
                <th>{t('duties.accounts.owner')}</th>
                <th>{t('duties.accounts.scope')}</th>
                <th>{t('duties.accounts.age')}</th>
                <th>{t('duties.accounts.status')}</th>
              </tr>
            </thead>
            <tbody>
              {accounts.data.company.map((row, i) => (
                <tr key={`${row.kind}-${i}`} className="border-t border-surface-700">
                  <td className="py-1">{t(`duties.accounts.kinds.${row.kind}`, { defaultValue: row.kind })}</td>
                  <td>{row.name ?? '—'}</td>
                  <td>{row.owner ?? '—'}</td>
                  <td>{row.scope}</td>
                  <td>{row.age_days == null ? '—' : t('duties.accounts.days', { count: row.age_days })}</td>
                  <td>{row.status ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      <ul className="space-y-1 text-xs text-gray-400" aria-label={t('duties.accounts.deployment')}>
        {accounts.data.deployment.map(row => <li key={row.kind}>{deploymentLine(row, t)}</li>)}
      </ul>
    </section>
  )
}

function deploymentLine(row: ServiceAccount, t: (key: string, options?: Record<string, unknown>) => string): string {
  if (row.kind === 'llm_proxy_token') {
    return t(row.present ? 'duties.accounts.proxyPresent' : 'duties.accounts.proxyMissing')
  }
  return t(`duties.accounts.kinds.${row.kind}`, { defaultValue: row.kind }) + ': ' + (row.name ?? '—') + ' · ' + (row.method ?? '—')
}
