import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { CircleSlash, FileSearch, GitBranch, Loader2, RefreshCw, Search, TriangleAlert } from 'lucide-react'
import { useCapabilities, useLineage } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { CountPill, EmptyState, PageHeader, Panel, StatusBadge, Toolbar } from '../components/ui'

/** Link target for the decisions recorded on a file. */
export function lineagePath(filePath: string): string {
  return `/lineage?${new URLSearchParams({ file: filePath })}`
}

/**
 * File lineage: the agent decisions recorded on a file in the knowledge graph
 * (`GET /v1/enterprise/lineage`). The file comes from the `file` query
 * parameter, so a claimed file links straight here.
 */
export default function Lineage() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const { data: capabilities } = useCapabilities()
  const [params, setParams] = useSearchParams()
  const file = params.get('file') ?? ''
  const [draft, setDraft] = useState(file)
  const lineage = useLineage(file, companyId)
  const entries = lineage.data?.lineage ?? []

  function submit(event: React.FormEvent) {
    event.preventDefault()
    const value = draft.trim()
    if (value) setParams({ file: value })
  }

  return (
    <div className="page-container max-w-4xl space-y-4">
      <PageHeader
        title={t('lineage.title', { defaultValue: 'File lineage' })}
        subtitle={t('lineage.subtitle', { defaultValue: 'The agent decisions recorded on a file, newest first (up to 10).' })}
      />

      {capabilities && !capabilities.memory_graph && (
        <p role="status" className="alert-warning items-center py-2.5 text-[13px]">
          <TriangleAlert size={15} aria-hidden="true" className="flex-shrink-0" />
          {t('lineage.unavailable', { defaultValue: 'File lineage needs the knowledge graph, which this deployment does not record.' })}
        </p>
      )}

      <form onSubmit={submit}>
        <Toolbar label={t('lineage.file', { defaultValue: 'File path' })}>
          <label className="min-w-0 flex-1">
            <span className="sr-only">{t('lineage.file', { defaultValue: 'File path' })}</span>
            <input value={draft} onChange={e => setDraft(e.target.value)} className="input control-sm w-full font-mono"
              placeholder={t('lineage.filePlaceholder', { defaultValue: 'src/app/main.py' })} />
          </label>
          <button type="submit" disabled={!draft.trim()} className="btn btn-primary btn-sm h-8">
            <Search size={13} aria-hidden="true" /> {t('lineage.search', { defaultValue: 'Show' })}
          </button>
        </Toolbar>
      </form>

      {!file ? null : lineage.isLoading ? (
        <div role="status" className="panel flex items-center gap-2 p-4 text-xs text-gray-400">
          <Loader2 size={14} aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> {t('lineage.loading', { defaultValue: 'Loading decisions…' })}
        </div>
      ) : lineage.isError ? (
        <div role="alert" className="alert-error items-center text-[13px]">
          <span>{t('lineage.error', { message: errorDetail(lineage.error), defaultValue: 'Could not load the lineage: {{message}}' })}</span>
          <button type="button" onClick={() => lineage.refetch()} className="btn-tool ml-auto">
            <RefreshCw size={13} aria-hidden="true" />{t('common.retry')}
          </button>
        </div>
      ) : lineage.data?.recorded === false ? (
        <Panel>
          <div role="status">
            <EmptyState icon={CircleSlash}
              title={t('lineage.notRecorded', { file, defaultValue: 'Not recorded: the knowledge graph is off, so no decisions are kept for {{file}}.' })} />
          </div>
        </Panel>
      ) : (
        <Panel
          title={<span className="flex min-w-0 items-center gap-1.5"><GitBranch size={13} aria-hidden="true" className="flex-shrink-0 text-gray-400" /><span className="truncate font-mono">{file}</span></span>}
          count={<CountPill count={entries.length} label={t('lineage.count', { count: entries.length })} />}>
          {entries.length === 0 ? (
            <EmptyState icon={FileSearch} title={t('lineage.empty', { file, defaultValue: 'No decisions recorded for {{file}}.' })} />
          ) : (
            <ol className="data-list" aria-label={t('lineage.listLabel', { file, defaultValue: 'Decisions on {{file}}' })}>
              {entries.map(entry => (
                <li key={entry.engram_id} className="data-row">
                  <div className="text-meta mb-1 flex flex-wrap items-center gap-2">
                    <span className="chip">{entry.role ?? t('lineage.unknownRole', { defaultValue: 'unknown role' })}</span>
                    {entry.timestamp && <time className="tabular-nums" dateTime={entry.timestamp}>{new Date(entry.timestamp).toLocaleString()}</time>}
                    {entry.legacy && (
                      <span title={t('lineage.legacyHint', { defaultValue: 'Recorded before decisions carried a company.' })}>
                        <StatusBadge icon={TriangleAlert} tone="warning" label={t('lineage.legacy', { defaultValue: 'no company' })} />
                      </span>
                    )}
                  </div>
                  <p className="whitespace-pre-wrap break-words text-[13px] text-gray-200">
                    {entry.decision || t('lineage.noSummary', { defaultValue: 'No summary recorded.' })}
                  </p>
                </li>
              ))}
            </ol>
          )}
        </Panel>
      )}
    </div>
  )
}
