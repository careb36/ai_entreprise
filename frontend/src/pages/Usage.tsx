import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { BarChart3, Download, Loader2 } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { useUsage, useUsageExport } from '../hooks/useTeams'
import {
  errorDetail,
  USAGE_DIMENSIONS,
  type UsageDimension,
  type UsageNumbers,
  type UsagePeriod,
  type UsageReport,
  type UsageRow,
} from '../services/api'
import { formatCostCents, formatTokens } from '../services/cost'
import { useAppStore } from '../store/useAppStore'

const PERIODS: UsagePeriod[] = ['day', 'week', 'month']

/** `YYYY-MM-DD` of a UTC day: the report's days are UTC. */
function utcDay(moment: Date): string {
  return moment.toISOString().slice(0, 10)
}

function defaultRange(): { since: string; until: string } {
  const until = new Date()
  const since = new Date(until.getTime() - 29 * 24 * 60 * 60 * 1000)
  return { since: utcDay(since), until: utcDay(until) }
}

/**
 * Who used what, for Managers: tasks, runs, verdicts, rework, the people's
 * decisions and the tokens and cost the proxy reported, by period, person,
 * team, agent role, producer and served model. A cost that is not known is
 * counted apart and never shown as 0. The CSV export is audited.
 */
export default function Usage() {
  const { t, i18n } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canRead = role === 'Owner' || role === 'Manager'
  const [range, setRange] = useState(defaultRange)
  const [period, setPeriod] = useState<UsagePeriod>('week')
  const [dimension, setDimension] = useState<UsageDimension>('period')
  const query = { ...range, period }
  const usage = useUsage(companyId, query, canRead)
  const exporter = useUsageExport(companyId)

  function download() {
    exporter.mutate(query, {
      onSuccess: text => {
        const blob = new Blob([text], { type: 'text/csv;charset=utf-8' })
        const url = URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = `usage-${companyId}-${query.since}-${query.until}-${query.period}.csv`
        link.click()
        URL.revokeObjectURL(url)
      },
    })
  }

  return (
    <div className="page-container max-w-6xl">
      <div className="page-header mb-6">
        <div>
          <div className="flex items-center gap-2">
            <BarChart3 className="text-brand-400" size={22} />
            <h1 className="page-title">{t('usage.title')}</h1>
          </div>
          <p className="text-gray-400 mt-1">{t('usage.subtitle')}</p>
        </div>
      </div>
      {!canRead ? (
        <div role="status" className="card p-4 text-sm text-gray-300">
          {t('usage.managerOnly', { role: role ?? '—' })}
        </div>
      ) : (
        <div className="space-y-4">
          <form className="card p-4 flex flex-wrap items-end gap-3" aria-label={t('usage.filters')} onSubmit={e => e.preventDefault()}>
            <label className="block">
              <span className="input-label">{t('usage.since')}</span>
              <input type="date" className="input" value={range.since}
                onChange={e => setRange(r => ({ ...r, since: e.target.value }))} />
            </label>
            <label className="block">
              <span className="input-label">{t('usage.until')}</span>
              <input type="date" className="input" value={range.until}
                onChange={e => setRange(r => ({ ...r, until: e.target.value }))} />
            </label>
            <label className="block">
              <span className="input-label">{t('usage.period')}</span>
              <select className="select" value={period} onChange={e => setPeriod(e.target.value as UsagePeriod)}>
                {PERIODS.map(p => <option key={p} value={p}>{t(`usage.periods.${p}`)}</option>)}
              </select>
            </label>
            <span className="text-xs text-gray-400">{t('usage.utc')}</span>
            <button type="button" className="btn-secondary btn-sm ml-auto" onClick={download} disabled={exporter.isPending}>
              <Download size={14} /> {t('usage.export')}
            </button>
          </form>
          {exporter.isError && <div role="alert" className="alert-error text-sm">{errorDetail(exporter.error)}</div>}
          {usage.isLoading ? (
            <Loader2 className="animate-spin text-brand-400" size={20} />
          ) : usage.isError ? (
            <div role="alert" className="alert-error text-sm">{errorDetail(usage.error)}</div>
          ) : usage.data ? (
            <>
              <Totals totals={usage.data.totals} locale={i18n.language} />
              <div role="tablist" aria-label={t('usage.groupBy')} className="flex flex-wrap gap-2">
                {USAGE_DIMENSIONS.map(d => (
                  <button key={d} type="button" role="tab" aria-selected={dimension === d}
                    className={`btn-sm ${dimension === d ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setDimension(d)}>
                    {t(`usage.dimensions.${d}`)}
                  </button>
                ))}
              </div>
              <UsageTable report={usage.data} dimension={dimension} locale={i18n.language} />
              <p className="text-xs text-gray-400">
                {t('usage.generated', { at: usage.data.generated_at })} {t('usage.costNote')}
              </p>
            </>
          ) : null}
        </div>
      )}
    </div>
  )
}

function Cost({ numbers, locale }: { numbers: UsageNumbers; locale: string }) {
  const { t } = useTranslation()
  const shown = formatCostCents(numbers.cost_cents, locale)
  return (
    <span>
      {shown ?? t('usage.costUnknown')}
      {numbers.cost_unknown_turns > 0 && shown !== null && (
        <span className="text-gray-400"> {t('usage.plusUnknown', { count: numbers.cost_unknown_turns })}</span>
      )}
    </span>
  )
}

function Totals({ totals, locale }: { totals: UsageNumbers; locale: string }) {
  const { t } = useTranslation()
  const tiles: [string, ReactNode][] = [
    [t('usage.columns.tasks'), totals.tasks],
    [t('usage.columns.runs'), totals.runs],
    [t('usage.columns.verified'), `${totals.verified} / ${totals.refuted}`],
    [t('usage.columns.tokens'), formatTokens(totals.reported_tokens)],
    [t('usage.columns.cost'), <Cost key="cost" numbers={totals} locale={locale} />],
  ]
  return (
    <div className="grid gap-3 grid-cols-2 sm:grid-cols-5">
      {tiles.map(([label, value]) => (
        <div key={label} className="card p-3">
          <div className="text-xs text-gray-400">{label}</div>
          <div className="text-lg text-gray-100">{value}</div>
        </div>
      ))}
    </div>
  )
}

function rowLabel(row: UsageRow, dimension: UsageDimension, t: (key: string) => string): string {
  if (row.key === null) return dimension === 'team' ? t('usage.noTeam') : t('usage.unattributed')
  return row.label ?? String(row.key)
}

function UsageTable({ report, dimension, locale }: { report: UsageReport; dimension: UsageDimension; locale: string }) {
  const { t } = useTranslation()
  const rows = report[`by_${dimension}`]
  if (rows.length === 0) return <p className="text-sm text-gray-400">{t('usage.empty')}</p>
  return (
    <div className="card p-0 overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-400">
            <th className="px-3 py-2 font-normal">{t(`usage.dimensions.${dimension}`)}</th>
            <th className="px-3 py-2 font-normal text-right">{t('usage.columns.tasks')}</th>
            <th className="px-3 py-2 font-normal text-right">{t('usage.columns.rework')}</th>
            <th className="px-3 py-2 font-normal text-right">{t('usage.columns.runs')}</th>
            <th className="px-3 py-2 font-normal text-right">{t('usage.columns.verifiedShort')}</th>
            <th className="px-3 py-2 font-normal text-right">{t('usage.columns.refutedShort')}</th>
            <th className="px-3 py-2 font-normal text-right">{t('usage.columns.decisions')}</th>
            <th className="px-3 py-2 font-normal text-right">{t('usage.columns.tokens')}</th>
            <th className="px-3 py-2 font-normal text-right">{t('usage.columns.estimated')}</th>
            <th className="px-3 py-2 font-normal text-right">{t('usage.columns.cost')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={String(row.key)} className="border-t border-surface-800">
              <td className="px-3 py-2 text-gray-200">{rowLabel(row, dimension, t)}</td>
              <td className="px-3 py-2 text-right">{row.tasks}</td>
              <td className="px-3 py-2 text-right">{row.rework}</td>
              <td className="px-3 py-2 text-right">{row.runs}</td>
              <td className="px-3 py-2 text-right">{row.verified}</td>
              <td className="px-3 py-2 text-right">{row.refuted}</td>
              <td className="px-3 py-2 text-right">{row.approvals + row.changes_requested}</td>
              <td className="px-3 py-2 text-right">{formatTokens(row.reported_tokens)}</td>
              <td className="px-3 py-2 text-right">{formatTokens(row.estimated_tokens)}</td>
              <td className="px-3 py-2 text-right"><Cost numbers={row} locale={locale} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
