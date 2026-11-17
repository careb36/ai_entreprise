import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Building2, Download, Loader2, OctagonX, RotateCcw } from 'lucide-react'
import { useQuotas, useQuotaUsageHistory, useResetQuota } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'
import { CountPill, EmptyState, PageHeader, Panel, StatusBadge } from './ui'


// No limit configured for a business unit.
const UNLIMITED = '∞'

export const QuotaPanel: React.FC = () => {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const { data: quotaData } = useQuotas(companyId)
  const [days, setDays] = useState(7)
  const { data: historyData } = useQuotaUsageHistory(companyId, days)
  const resetQuotaMutation = useResetQuota()
  const [pendingResets, setPendingResets] = useState<Set<number>>(new Set())

  const quotas = Array.isArray(quotaData) ? quotaData : []
  const history = historyData?.days ?? []
  const maxTokens = Math.max(1, ...history.map((d: any) => d.tokens))

  const exportCsv = () => {
    const rows = [['date', 'tokens']]
    for (const d of history) rows.push([d.date, String(d.tokens)])
    const csv = rows.map(r => r.join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `quota_usage_${companyId}_${days}d.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('governance.quotaTitle', { defaultValue: 'Token Quota Usage' })}
        actions={
          <button type="button" onClick={exportCsv} className="btn-tool" title={t('governance.exportCsv', { defaultValue: 'Export CSV' })}>
            <Download size={14} aria-hidden="true" /> CSV
          </button>
        }
      />

      {/* Per-unit quotas */}
      {quotas.length > 0 && (
        <Panel
          title={t('governance.quotaUnits', { defaultValue: 'Business units' })}
          count={<CountPill count={quotas.length} label={t('governance.quotaUnitCount', { count: quotas.length })} />}
        >
          <ul className="data-list">
            {quotas.map((unit: any) => {
              const pct = unit.limit > 0 ? Math.min(100, Math.round((unit.used / unit.limit) * 100)) : 0
              const isWarning = pct >= 80 && pct < 100
              const isExceeded = unit.limit > 0 && unit.used >= unit.limit
              const barColor = isExceeded ? 'bg-status-error' : isWarning ? 'bg-yellow-500' : 'bg-brand-500'
              return (
                <li key={unit.id ?? unit.unit} className="data-row">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[13px] font-semibold text-gray-100">{unit.unit}</span>
                    <div className="flex flex-wrap items-center gap-2">
                      {isExceeded && <StatusBadge icon={OctagonX} tone="danger" label={t('governance.quotaExceeded')} />}
                      {isWarning && !isExceeded && <StatusBadge icon={AlertTriangle} tone="warning" label={t('governance.quotaWarning')} />}
                      <span className="text-meta tabular-nums">
                        {unit.used.toLocaleString()} / {unit.limit > 0 ? unit.limit.toLocaleString() : UNLIMITED} {t('governance.tokens')}
                      </span>
                      {unit.id && (
                        <button
                          type="button"
                          onClick={() => {
                            setPendingResets(previous => new Set(previous).add(unit.id))
                            resetQuotaMutation.mutate(unit.id, {
                              onSettled: () => setPendingResets(previous => {
                                const next = new Set(previous)
                                next.delete(unit.id)
                                return next
                              }),
                            })
                          }}
                          disabled={pendingResets.has(unit.id)}
                          className="btn-tool w-8 justify-center px-0"
                          title={t('governance.resetQuota')}
                        >
                          {pendingResets.has(unit.id)
                            ? <Loader2 size={12} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
                            : <RotateCcw size={12} aria-hidden="true" />}
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-700" aria-hidden="true">
                    <div className={`h-full rounded-full transition-all duration-500 motion-reduce:transition-none ${barColor}`} style={{ width: `${unit.limit > 0 ? pct : 0}%` }} />
                  </div>
                  {unit.limit > 0 && <p className="text-meta mt-1 text-right tabular-nums">{pct}%</p>}
                </li>
              )
            })}
          </ul>
        </Panel>
      )}

      {/* Historical usage chart */}
      {history.length > 0 && (
        <Panel
          title={t('governance.usageHistory', { defaultValue: 'Usage history' })}
          actions={
            <select value={days} aria-label={t('governance.historyPeriod')} onChange={e => setDays(Number(e.target.value))} className="select control-sm w-auto">
              <option value={7}>{t('governance.lastDays', { count: 7, defaultValue: '{{count}} days' })}</option>
              <option value={14}>{t('governance.lastDays', { count: 14, defaultValue: '{{count}} days' })}</option>
              <option value={30}>{t('governance.lastDays', { count: 30, defaultValue: '{{count}} days' })}</option>
            </select>
          }
        >
          <div className="p-3">
            <div className="flex h-32 items-end gap-1">
              {history.map((d: any) => (
                <div key={d.date} className="flex h-full flex-1 flex-col items-center gap-1">
                  {/* The bar's column takes the chart's height, so its percentage has something to measure. */}
                  <div className="flex min-h-0 w-full flex-1 items-end">
                    <div
                      className="w-full rounded-t bg-brand-500/70 transition-all motion-reduce:transition-none"
                      style={{ height: `${Math.max(2, (d.tokens / maxTokens) * 100)}%` }}
                      title={t('governance.dayTokens', { date: d.date, tokens: d.tokens.toLocaleString() })}
                    />
                  </div>
                  <span className="mt-4 origin-left rotate-[-45deg] whitespace-nowrap text-[10px] tabular-nums text-gray-400">{d.date.slice(5)}</span>
                </div>
              ))}
            </div>
            <p className="text-meta mt-4 text-right tabular-nums">{t('governance.totalTokens', { total: historyData?.total?.toLocaleString() ?? 0, defaultValue: 'Total: {{total}} tokens' })}</p>
          </div>
        </Panel>
      )}

      {companyId === null && <EmptyState icon={Building2} dashed title={t('evidence.selectCompany')} />}
    </div>
  )
}
