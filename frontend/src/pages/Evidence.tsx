import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronRight, FileSearch, ListFilter, RefreshCw, X } from 'lucide-react'
import { useAgentRuns } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'
import { Verdict, VERDICT_PRESENTATION } from '../components/Verdict'
import { EmptyState, PageHeader, Panel, SegmentedFilter, Toolbar, type SegmentedOption } from '../components/ui'
import type { AgentRunListItem, AgentRunStatus } from '../services/api'

const STATUSES = Object.keys(VERDICT_PRESENTATION) as AgentRunStatus[]

/** One run: the Verifier's verdict first (what was observed), then what the producer submitted. */
function RunRow({ run }: { run: AgentRunListItem }) {
  const { t } = useTranslation()
  return (
    <li>
      <Link to={`/evidence/${run.id}`} className="data-row-link group flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
        <div className="flex-shrink-0 sm:w-32">
          <Verdict status={run.status} compact />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-[13px] text-gray-100">{run.external_ref}</p>
          <p className="text-meta mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="chip">{run.producer}</span>
            {run.agent_role && <span>{run.agent_role}</span>}
            <span className="tabular-nums">{t('evidence.ingestedAt')}: {new Date(run.ingested_at).toLocaleString()}</span>
          </p>
          {run.latest_approval && (
            <p className="mt-1 text-xs text-gray-300">
              {t('evidence.latestApproval')}: {t(`evidence.approval.${run.latest_approval.decision}`)}
              {' · '}{run.latest_approval.approver_name}
              {run.latest_approval.feedback ? ` — ${run.latest_approval.feedback}` : ''}
            </p>
          )}
        </div>
        <span className="hidden flex-shrink-0 items-center gap-0.5 text-xs font-medium text-brand-300 group-hover:text-brand-200 sm:inline-flex">
          {t('evidence.viewDetails')}<ChevronRight size={13} aria-hidden="true" />
        </span>
      </Link>
    </li>
  )
}

function EvidenceRuns({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const { data, isLoading, isError, refetch } = useAgentRuns(companyId, { limit: 500 })
  const [producer, setProducer] = useState('')
  const [status, setStatus] = useState<AgentRunStatus | ''>('')
  const [from, setFrom] = useState('')
  const [until, setUntil] = useState('')

  const runs = data?.agent_runs ?? []
  const producers = useMemo(() => [...new Set(runs.map(run => run.producer))].sort(), [runs])
  const counts = useMemo(() => {
    const result: Record<AgentRunStatus, number> = { verified: 0, refuted: 0, unverifiable: 0, ingested: 0 }
    runs.forEach(run => { result[run.status] += 1 })
    return result
  }, [runs])
  const filteredRuns = runs.filter(run => {
    const date = run.ingested_at.slice(0, 10)
    return (!producer || run.producer === producer)
      && (!status || run.status === status)
      && (!from || date >= from)
      && (!until || date <= until)
  })
  const hasFilters = Boolean(producer || status || from || until)

  function clearFilters() {
    setProducer('')
    setStatus('')
    setFrom('')
    setUntil('')
  }

  if (isLoading) {
    return <div role="status" className="space-y-2" aria-label={t('evidence.loading')}>
      {[1, 2, 3].map(item => <div key={item} className="skeleton h-16 rounded-xl" />)}
    </div>
  }

  if (isError) {
    return (
      <div role="alert" className="alert-error flex items-center justify-between gap-3">
        <span>{t('evidence.error')}</span>
        <button onClick={() => refetch()} className="btn-tool">
          <RefreshCw size={13} aria-hidden="true" />{t('evidence.retry')}
        </button>
      </div>
    )
  }

  // The refuted preset keeps its warning tone while not pressed, so the
  // number that needs a person never gets lost among the rest (isolation effect).
  const verdictOptions: SegmentedOption<AgentRunStatus | 'all'>[] = [
    { value: 'all', label: t('evidence.summary.all'), count: runs.length },
    ...STATUSES.map(value => ({
      value,
      label: t(`evidence.verdict.${value}`),
      icon: VERDICT_PRESENTATION[value].Icon,
      count: counts[value],
      attention: value === 'refuted',
    })),
  ]

  return (
    <div className="space-y-3">
      <Toolbar label={t('evidence.filters')} sticky>
        {runs.length > 0 && (
          <SegmentedFilter label={t('evidence.summary.aria')} options={verdictOptions} value={status || 'all'}
            testIdPrefix="summary"
            onChange={value => setStatus(value === 'all' || value === status ? '' : value)} />
        )}
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          <ListFilter size={13} aria-hidden="true" />
          <span className="sr-only">{t('evidence.producer')}</span>
          <select className="select control-sm w-auto pr-7" value={producer} onChange={event => setProducer(event.target.value)}>
            <option value="">{t('evidence.allProducers')}</option>
            {producers.map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          {t('evidence.ingestedAfter')}
          <input className="input control-sm w-[7.75rem]" type="date" value={from} onChange={event => setFrom(event.target.value)} />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          {t('evidence.ingestedBefore')}
          <input className="input control-sm w-[7.75rem]" type="date" value={until} onChange={event => setUntil(event.target.value)} />
        </label>
      </Toolbar>

      <Panel title={t('evidence.runsTitle')}
        actions={<>
          {runs.length > 0 && (
            <span className="text-xs tabular-nums text-gray-400">{t('evidence.showing', { shown: filteredRuns.length, total: runs.length })}</span>
          )}
          {hasFilters && (
            <button type="button" onClick={clearFilters} className="btn-tool">
              <X size={12} aria-hidden="true" />{t('evidence.clearFilters')}
            </button>
          )}
        </>}>
        {filteredRuns.length === 0 ? (
          <EmptyState icon={FileSearch} title={t('evidence.empty')} />
        ) : (
          <ul className="data-list">
            {filteredRuns.map(run => <RunRow key={run.id} run={run} />)}
          </ul>
        )}
      </Panel>
    </div>
  )
}

export default function Evidence() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)

  return (
    <div className="page-container max-w-6xl">
      <PageHeader title={t('evidence.title')} subtitle={t('evidence.subtitle')} />
      {companyId === null
        ? <EmptyState icon={FileSearch} title={t('evidence.selectCompany')} dashed />
        : <EvidenceRuns companyId={companyId} />}
    </div>
  )
}
