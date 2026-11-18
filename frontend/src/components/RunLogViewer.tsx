import { useEffect, useId, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight, Download, FileText, FileX, Loader2, ShieldAlert, ShieldCheck, ShieldQuestion, type LucideIcon } from 'lucide-react'
import { CountPill, EmptyState, Panel, StatusBadge, type StatusTone } from './ui'
import { refusalOf, useRunLog, useRunLogs, type LogIntegrity, type RecordedLog } from '../services/runLogs'

/** Lines shown per page of a log. */
export const LINES_PER_PAGE = 500

const INTEGRITY: Record<LogIntegrity, { icon: LucideIcon; tone: StatusTone }> = {
  matched: { icon: ShieldCheck, tone: 'success' },
  mismatched: { icon: ShieldAlert, tone: 'danger' },
  missing: { icon: FileX, tone: 'warning' },
  unavailable: { icon: ShieldQuestion, tone: 'neutral' },
}

/** What the backend observed when it served the log: never computed here. */
export function IntegrityBadge({ integrity }: { integrity: LogIntegrity }) {
  const { t } = useTranslation()
  const { icon, tone } = INTEGRITY[integrity]
  return <StatusBadge icon={icon} tone={tone} label={t(`runLogs.integrity.${integrity}`)} />
}

function Sha({ label, value }: { label: string; value: string | null | undefined }) {
  const { t } = useTranslation()
  return (
    <div className="min-w-0">
      <dt className="text-meta text-gray-400">{label}</dt>
      <dd className="break-all font-mono text-xs text-gray-300">{value || t('runLogs.notRecorded')}</dd>
    </div>
  )
}

/** One full log, read back by the backend: integrity, search, pages and a download. */
function LogBody({ companyId, runId, log }: { companyId: number; runId: number; log: RecordedLog }) {
  const { t } = useTranslation()
  const searchId = useId()
  const query = useRunLog(companyId, runId, log.id)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const served = query.data
  const lines = useMemo(() => (served ? served.text.split('\n') : []), [served])
  const matches = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const numbered = lines.map((text, index) => ({ text, number: index + 1 }))
    return needle ? numbered.filter(line => line.text.toLowerCase().includes(needle)) : numbered
  }, [lines, search])
  const pages = Math.max(1, Math.ceil(matches.length / LINES_PER_PAGE))
  const current = Math.min(page, pages - 1)
  const shown = matches.slice(current * LINES_PER_PAGE, (current + 1) * LINES_PER_PAGE)
  const [href, setHref] = useState<string | null>(null)

  useEffect(() => {
    if (!served) return undefined
    const url = URL.createObjectURL(served.blob)
    setHref(url)
    return () => URL.revokeObjectURL(url)
  }, [served])

  if (query.isLoading) {
    return <p role="status" className="flex items-center gap-2 p-3 text-xs text-gray-400"><Loader2 size={14} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />{t('runLogs.reading')}</p>
  }
  if (query.isError || !served) {
    const refused = refusalOf(query.error)
    return (
      <div className="space-y-2 p-3">
        <IntegrityBadge integrity={refused.integrity} />
        <p role="alert" className="text-xs text-gray-300">{t(`runLogs.refused.${refused.integrity}`)} {refused.reason}</p>
        <dl><Sha label={t('runLogs.recordedSha')} value={refused.recordedSha256 ?? log.recorded_sha256} /></dl>
      </div>
    )
  }

  return (
    <div className="space-y-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <IntegrityBadge integrity={served.integrity} />
        <span className="text-meta tabular-nums text-gray-400">{t('runLogs.observedBy', { size: served.size, by: served.observedBy ?? '—' })}</span>
        {href && (
          <a href={href} download={served.filename} className="btn-tool ml-auto">
            <Download size={14} aria-hidden="true" />{t('runLogs.download')}
          </a>
        )}
      </div>
      <dl className="grid gap-2 sm:grid-cols-2">
        <Sha label={t('runLogs.recordedSha')} value={served.recordedSha256} />
        <Sha label={t('runLogs.observedSha')} value={served.observedSha256} />
      </dl>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={searchId} className="sr-only">{t('runLogs.search')}</label>
        <input id={searchId} type="search" className="input control-sm min-w-0 flex-1" placeholder={t('runLogs.search')}
          value={search} onChange={event => { setSearch(event.target.value); setPage(0) }} />
        <span role="status" className="text-meta tabular-nums text-gray-400">
          {t('runLogs.lines', { count: matches.length, total: lines.length })}
        </span>
        <button type="button" className="btn-tool" disabled={current === 0} onClick={() => setPage(current - 1)} aria-label={t('runLogs.previous')}>
          <ChevronLeft size={14} aria-hidden="true" />
        </button>
        <span className="text-meta tabular-nums text-gray-400">{t('runLogs.page', { page: current + 1, pages })}</span>
        <button type="button" className="btn-tool" disabled={current >= pages - 1} onClick={() => setPage(current + 1)} aria-label={t('runLogs.next')}>
          <ChevronRight size={14} aria-hidden="true" />
        </button>
      </div>
      <pre data-testid="run-log-lines" className="max-h-[32rem] overflow-auto rounded-md border border-surface-700 bg-surface-950 p-2 font-mono text-xs leading-5 text-gray-200">
        {shown.map(line => (
          <div key={line.number} className="flex gap-3">
            <span aria-hidden="true" className="w-12 flex-shrink-0 select-none text-right tabular-nums text-gray-400">{line.number}</span>
            <span className="whitespace-pre-wrap break-all">{line.text}</span>
          </div>
        ))}
      </pre>
    </div>
  )
}

/**
 * A run's full logs (issue #315): the install, the test run and the
 * application check kept each stream whole, and the backend recorded its
 * sha256. Opening one reads it back; the badge is the backend's comparison
 * of the bytes it served with that record.
 */
export function RunLogViewer({ companyId, runId }: { companyId: number; runId: number }) {
  const { t } = useTranslation()
  const logsQuery = useRunLogs(companyId, runId)
  const [open, setOpen] = useState<string | null>(null)
  const logs = logsQuery.data?.logs ?? []

  return (
    <Panel title={t('runLogs.title')} count={<CountPill count={logs.length} label={t('runLogs.count', { count: logs.length })} />}>
      {logsQuery.isLoading ? <div className="skeleton m-3 h-12 rounded" /> :
        logsQuery.isError ? <p role="alert" className="p-3 text-xs text-red-300">{t('runLogs.loadError')}</p> :
          logs.length === 0 ? <EmptyState icon={FileText} title={t('runLogs.empty')} description={t('runLogs.emptyHint')} /> : (
            <ul className="data-list">
              {logs.map(log => (
                <li key={log.id} className="data-row">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="chip">{log.source}</span>
                    <span className="text-xs font-medium text-white">{log.stream ?? t('runLogs.noStream')}</span>
                    {typeof log.recorded_size === 'number' && (
                      <span className="text-meta tabular-nums text-gray-400">{t('runLogs.size', { size: log.recorded_size })}</span>
                    )}
                    {log.truncated_by_daemon && <span className="text-meta text-amber-300">{t('runLogs.truncated')}</span>}
                    {log.openable ? (
                      <button type="button" className={`btn-tool ml-auto ${open === log.id ? 'btn-tool-on' : ''}`} aria-expanded={open === log.id}
                        onClick={() => setOpen(open === log.id ? null : log.id)}>
                        {t(open === log.id ? 'runLogs.close' : 'runLogs.open')}
                      </button>
                    ) : (
                      <span className="ml-auto text-meta text-gray-400">{t('runLogs.notKept', { reason: log.reason ?? '' })}</span>
                    )}
                  </div>
                  {open === log.id && <LogBody companyId={companyId} runId={runId} log={log} />}
                </li>
              ))}
            </ul>
          )}
    </Panel>
  )
}
