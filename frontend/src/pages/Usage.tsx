import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { BarChart3, CalendarDays, Download, Loader2, Lock } from 'lucide-react'
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
import { CountPill, EmptyState, PageHeader, Panel, Toolbar } from '../components/ui'

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
    <div className="page-container max-w-6xl space-y-4">
      <PageHeader title={t('usage.title')} subtitle={t('usage.subtitle')} />
      {!canRead ? (
        <div role="status">
          <EmptyState icon={Lock} dashed title={t('usage.managerOnly', { role: role ?? '—' })} />
        </div>
      ) : (
        <>
          <form onSubmit={e => e.preventDefault()}>
            <Toolbar label={t('usage.filters')}>
              <label className="flex items-center gap-1.5 text-xs text-gray-400">
                {t('usage.since')}
                <input type="date" className="input control-sm w-[8.5rem]" value={range.since}
                  onChange={e => setRange(r => ({ ...r, since: e.target.value }))} />
              </label>
              <label className="flex items-center gap-1.5 text-xs text-gray-400">
                {t('usage.until')}
                <input type="date" className="input control-sm w-[8.5rem]" value={range.until}
                  onChange={e => setRange(r => ({ ...r, until: e.target.value }))} />
              </label>
              <label className="flex items-center gap-1.5 text-xs text-gray-400">
                {t('usage.period')}
                <select className="select control-sm w-auto pr-7" value={period} onChange={e => setPeriod(e.target.value as UsagePeriod)}>
                  {PERIODS.map(p => <option key={p} value={p}>{t(`usage.periods.${p}`)}</option>)}
                </select>
              </label>
              <span className="text-meta flex items-center gap-1">
                <CalendarDays size={11} aria-hidden="true" />{t('usage.utc')}
              </span>
              <button type="button" className="btn-tool ml-auto border border-surface-700" onClick={download} disabled={exporter.isPending}>
                <Download size={13} aria-hidden="true" /> {t('usage.export')}
              </button>
            </Toolbar>
          </form>
          {exporter.isError && <div role="alert" className="alert-error text-[13px]">{errorDetail(exporter.error)}</div>}
          {usage.isLoading ? (
            <div role="status" aria-label={t('common.loading', { defaultValue: 'Loading...' })} className="panel flex items-center justify-center p-6">
              <Loader2 className="animate-spin text-brand-400 motion-reduce:animate-none" size={18} aria-hidden="true" />
            </div>
          ) : usage.isError ? (
            <div role="alert" className="alert-error text-[13px]">{errorDetail(usage.error)}</div>
          ) : usage.data ? (
            <>
              <Totals totals={usage.data.totals} locale={i18n.language} />
              <UsageTable report={usage.data} dimension={dimension} onDimension={setDimension} locale={i18n.language} />
              <p className="text-meta">
                {t('usage.generated', { at: usage.data.generated_at })} {t('usage.costNote')}
              </p>
            </>
          ) : null}
        </>
      )}
    </div>
  )
}

function Cost({ numbers, locale }: { numbers: UsageNumbers; locale: string }) {
  const { t } = useTranslation()
  const shown = formatCostCents(numbers.cost_cents, locale)
  return (
    <span>
      {shown ?? <span className="text-gray-400">{t('usage.costUnknown')}</span>}
      {numbers.cost_unknown_turns > 0 && shown !== null && (
        <span className="text-[11px] font-normal text-gray-400"> {t('usage.plusUnknown', { count: numbers.cost_unknown_turns })}</span>
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
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-5">
      {tiles.map(([label, value]) => (
        <div key={label} className="panel flex flex-col-reverse px-3 py-2.5">
          <dd className="mt-1 text-lg font-semibold leading-tight tabular-nums text-gray-100">{value}</dd>
          <dt className="text-meta font-medium">{label}</dt>
        </div>
      ))}
    </dl>
  )
}

function rowLabel(row: UsageRow, dimension: UsageDimension, t: (key: string) => string): string {
  if (row.key === null) return dimension === 'team' ? t('usage.noTeam') : t('usage.unattributed')
  return row.label ?? String(row.key)
}

const TH = 'px-3 py-2 text-[11px] font-medium text-gray-400'
const TD = 'px-3 py-2 text-right tabular-nums'

function UsageTable({ report, dimension, onDimension, locale }: {
  report: UsageReport
  dimension: UsageDimension
  onDimension: (dimension: UsageDimension) => void
  locale: string
}) {
  const { t } = useTranslation()
  const rows = report[`by_${dimension}`]
  return (
    <Panel>
      <div className="panel-header">
        <div role="tablist" aria-label={t('usage.groupBy')} className="segmented">
          {USAGE_DIMENSIONS.map(d => (
            <button key={d} type="button" role="tab" aria-selected={dimension === d}
              className={`segmented-item ${dimension === d ? 'bg-surface-700 text-white shadow-sm hover:bg-surface-700' : ''}`}
              onClick={() => onDimension(d)}>
              {t(`usage.dimensions.${d}`)}
            </button>
          ))}
        </div>
        <CountPill className="ml-auto" count={rows.length} label={t('usage.rowCount', { count: rows.length })} />
      </div>
      {rows.length === 0 ? (
        <EmptyState icon={BarChart3} title={t('usage.empty')} />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr>
                <th className={`${TH} text-left`} scope="col">{t(`usage.dimensions.${dimension}`)}</th>
                <th className={`${TH} text-right`} scope="col">{t('usage.columns.tasks')}</th>
                <th className={`${TH} text-right`} scope="col">{t('usage.columns.rework')}</th>
                <th className={`${TH} text-right`} scope="col">{t('usage.columns.runs')}</th>
                <th className={`${TH} text-right`} scope="col">{t('usage.columns.verifiedShort')}</th>
                <th className={`${TH} text-right`} scope="col">{t('usage.columns.refutedShort')}</th>
                <th className={`${TH} text-right`} scope="col">{t('usage.columns.decisions')}</th>
                <th className={`${TH} text-right`} scope="col">{t('usage.columns.tokens')}</th>
                <th className={`${TH} text-right`} scope="col">{t('usage.columns.estimated')}</th>
                <th className={`${TH} text-right`} scope="col">{t('usage.columns.cost')}</th>
              </tr>
            </thead>
            <tbody className="text-gray-300">
              {rows.map(row => (
                <tr key={String(row.key)} className="border-t border-surface-700 transition-colors hover:bg-surface-800/40 motion-reduce:transition-none">
                  <td className="px-3 py-2 text-[13px] text-gray-100">{rowLabel(row, dimension, t)}</td>
                  <td className={TD}>{row.tasks}</td>
                  <td className={TD}>{row.rework}</td>
                  <td className={TD}>{row.runs}</td>
                  <td className={TD}>{row.verified}</td>
                  <td className={TD}>{row.refuted}</td>
                  <td className={TD}>{row.approvals + row.changes_requested}</td>
                  <td className={TD}>{formatTokens(row.reported_tokens)}</td>
                  <td className={TD}>{formatTokens(row.estimated_tokens)}</td>
                  <td className={TD}><Cost numbers={row} locale={locale} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
