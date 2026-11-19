import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, Minus } from 'lucide-react'
import { errorDetail } from '../services/api'
import { useCatalogEntry, useCatalogProviders, type CatalogModel } from '../services/modelCatalog'
import { EmptyState, LoadError, LoadingRows, PageHeader, Panel, TABLE, TD, TH, TR, Toolbar } from '../components/ui'

const CAPABILITIES = ['tool_call', 'reasoning', 'structured_output', 'attachment', 'open_weights'] as const

/** A capability as an icon and a word for screen readers: never the icon alone. */
function Capability({ on, label }: { on: boolean | undefined; label: string }) {
  const { t } = useTranslation()
  if (on === undefined) return <span className="text-gray-500">—<span className="sr-only">{t('modelCatalog.unknown', { capability: label })}</span></span>
  return on
    ? <span className="text-emerald-300"><Check size={13} aria-hidden="true" /><span className="sr-only">{t('modelCatalog.yes', { capability: label })}</span></span>
    : <span className="text-gray-400"><Minus size={13} aria-hidden="true" /><span className="sr-only">{t('modelCatalog.no', { capability: label })}</span></span>
}

/**
 * The model catalogue (`/company/model-catalog`): the providers the public
 * catalogue knows and, for the one chosen, its models with what each can do,
 * its limits and its list prices (`GET /v1/models/catalog/{provider}`). A
 * reference: it says what a model can do, not that a provider serves it to
 * this company, which only a validated connection on the Models page shows.
 */
export default function ModelCatalog() {
  const { t, i18n } = useTranslation()
  const providers = useCatalogProviders()
  const [providerId, setProviderId] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const entry = useCatalogEntry(providerId)
  const ids = { provider: useId(), filter: useId() }
  const number = new Intl.NumberFormat(i18n.language)
  const money = new Intl.NumberFormat(i18n.language, { style: 'currency', currency: 'USD', maximumFractionDigits: 4 })
  const models = (entry.data?.models ?? []).filter(m => !filter.trim() || `${m.id} ${m.name ?? ''}`.toLowerCase().includes(filter.trim().toLowerCase()))

  return (
    <div className="page-container max-w-6xl space-y-4">
      <PageHeader title={t('modelCatalog.title')} subtitle={t('modelCatalog.subtitle')} />
      {providers.isLoading ? <LoadingRows rows={1} label={t('modelCatalog.loading')} />
        : providers.isError ? <LoadError onRetry={() => providers.refetch()}>{t('modelCatalog.loadError', { message: errorDetail(providers.error) })}</LoadError>
          : providers.data && (
            <Toolbar label={t('modelCatalog.choose')}>
              <label htmlFor={ids.provider} className="text-xs text-gray-300">{t('modelCatalog.provider')}</label>
              <select id={ids.provider} value={providerId ?? ''} onChange={event => setProviderId(event.target.value || null)} className="select control-sm">
                <option value="">{t('modelCatalog.pick')}</option>
                {providers.data.providers.map(p => <option key={p.id} value={p.id}>{t('modelCatalog.providerOption', { name: p.name, count: p.model_count })}</option>)}
              </select>
              {providerId && (
                <>
                  <label htmlFor={ids.filter} className="text-xs text-gray-300">{t('modelCatalog.filter')}</label>
                  <input id={ids.filter} value={filter} onChange={event => setFilter(event.target.value)} className="input control-sm w-48" />
                </>
              )}
            </Toolbar>
          )}
      {providerId === null ? (
        providers.data && <EmptyState title={t('modelCatalog.none')} description={t('modelCatalog.source', { origin: providers.data.origin })} />
      ) : entry.isLoading ? <LoadingRows rows={2} label={t('modelCatalog.loadingProvider')} />
        : entry.isError ? <LoadError onRetry={() => entry.refetch()}>{errorDetail(entry.error)}</LoadError>
          : entry.data && (
            <Panel title={entry.data.name}>
              <p className="border-b border-surface-700 px-3 py-2 text-xs text-gray-400">
                {entry.data.base_url ? t('modelCatalog.baseUrl', { url: entry.data.base_url }) : t('modelCatalog.noBaseUrl')}
              </p>
              {models.length === 0 ? <EmptyState title={t('modelCatalog.noModels')} /> : (
                <div className="overflow-x-auto">
                  <table className={TABLE}>
                    <caption className="sr-only">{t('modelCatalog.caption', { name: entry.data.name })}</caption>
                    <thead>
                      <tr>
                        <th scope="col" className={TH}>{t('modelCatalog.columns.model')}</th>
                        {CAPABILITIES.map(c => <th key={c} scope="col" className={`${TH} text-center`}>{t(`modelCatalog.capabilities.${c}`)}</th>)}
                        <th scope="col" className={`${TH} text-right`}>{t('modelCatalog.columns.context')}</th>
                        <th scope="col" className={`${TH} text-right`}>{t('modelCatalog.columns.price')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {models.map((m: CatalogModel) => (
                        <tr key={m.id} className={TR}>
                          <td className={TD}>
                            <div className="text-gray-100">{m.name ?? m.id}</div>
                            <code className="text-meta font-mono">{m.id}</code>
                          </td>
                          {CAPABILITIES.map(c => <td key={c} className={`${TD} text-center`}><Capability on={m[c]} label={t(`modelCatalog.capabilities.${c}`)} /></td>)}
                          <td className={`${TD} text-right tabular-nums`}>{m.context === undefined ? '—' : number.format(m.context)}</td>
                          <td className={`${TD} text-right tabular-nums`}>
                            {m.cost_input === undefined && m.cost_output === undefined ? '—'
                              : t('modelCatalog.price', { input: m.cost_input === undefined ? '—' : money.format(m.cost_input), output: m.cost_output === undefined ? '—' : money.format(m.cost_output) })}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="text-meta border-t border-surface-700 px-3 py-2">{t('modelCatalog.claimNote')}</p>
            </Panel>
          )}
    </div>
  )
}
