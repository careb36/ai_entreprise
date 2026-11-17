import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { errorDetail, type MemoryResult } from '../services/api'
import { useSearchMemory, useStoreMemory } from '../hooks/useQueries'
import { Search, Database, ChevronDown, ChevronUp, Save, SearchX } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { LoadErrorAlert, Spinner } from '../components/knowledge/PageStates'
import { CountPill, EmptyState, PageHeader, Panel, Toolbar } from '../components/ui'

// The API takes the note as a query parameter, so long text is capped.
const MAX_NOTE_LENGTH = 2000

function MetadataView({ metadata }: { metadata: unknown }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  if (!metadata) return null

  let text: string
  try {
    text = typeof metadata === 'string' ? metadata : JSON.stringify(metadata, null, 2)
  } catch {
    text = String(metadata)
  }

  return (
    <div className="mt-1.5">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
        className="focus-ring inline-flex items-center gap-1 rounded-sm text-[11px] text-gray-400 transition-colors hover:text-gray-200 motion-reduce:transition-none"
      >
        {open ? <ChevronUp size={12} aria-hidden="true" /> : <ChevronDown size={12} aria-hidden="true" />}
        {t('memorySearch.metadata', { defaultValue: 'metadata' })}
      </button>
      {open && (
        <pre className="mt-1 max-h-40 overflow-auto rounded-md border border-surface-700 bg-surface-950/60 p-2 font-mono text-xs text-gray-300">
          {text}
        </pre>
      )}
    </div>
  )
}

/** Similarity as a word and a figure; the tone only repeats what the figure says. */
function scoreClass(s: number) {
  if (s >= 0.85) return 'text-emerald-300'
  if (s >= 0.65) return 'text-amber-300'
  return 'text-gray-400'
}

export default function MemorySearch() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(10)
  const [submitted, setSubmitted] = useState<{ query: string; limit: number } | null>(null)
  const search = useSearchMemory(submitted?.query ?? '', companyId, submitted?.limit ?? limit)
  const results: MemoryResult[] = search.data?.results ?? []

  function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    if (!query.trim()) return
    setSubmitted({ query: query.trim(), limit })
  }

  return (
    <div className="page-container max-w-4xl space-y-4">
      <PageHeader title={t('memorySearch.title')} subtitle={t('memorySearch.subtitle')} />

      <form onSubmit={handleSearch}>
        <Toolbar label={t('memorySearch.query')}>
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={t('memorySearch.queryPlaceholder')}
            aria-label={t('memorySearch.queryPlaceholder')}
            className="input control-sm min-w-0 flex-1 basis-56"
          />
          <label className="flex items-center gap-1.5 text-xs text-gray-400">
            <span aria-hidden="true">{t('memorySearch.limit')}</span>
            <input
              type="number"
              value={limit}
              onChange={e => setLimit(parseInt(e.target.value) || 10)}
              min={1} max={100}
              className="input control-sm w-20 tabular-nums"
              title={t('memorySearch.limit')}
              aria-label={t('memorySearch.limit')}
            />
          </label>
          <button
            type="submit"
            disabled={search.isFetching || !query.trim()}
            className="btn btn-primary btn-sm h-8"
          >
            {search.isFetching ? <Spinner /> : <Search size={14} aria-hidden="true" />}
            {search.isFetching ? t('memorySearch.searching') : t('memorySearch.search')}
          </button>
        </Toolbar>
      </form>

      {search.isError && (
        <LoadErrorAlert onRetry={() => void search.refetch()}>
          {t('memorySearch.errorDetail', { message: errorDetail(search.error), defaultValue: 'Search failed: {{message}}' })}
        </LoadErrorAlert>
      )}

      {!submitted && (
        <EmptyState icon={Database} title={t('memorySearch.query', 'Enter a query to search memory')} dashed />
      )}

      {search.isFetching && (
        <div role="status" aria-label={t('memorySearch.searching')} className="space-y-2">
          {[1, 2, 3].map(i => <div key={i} className="skeleton h-16 rounded-xl motion-reduce:animate-none" />)}
        </div>
      )}

      {submitted && search.isSuccess && !search.isFetching && (
        <Panel title={t('memorySearch.results')}
          count={<CountPill count={results.length} label={t('memorySearch.resultCount', { count: results.length, defaultValue: '{{count}} results' })} />}>
          {results.length === 0 ? (
            <EmptyState icon={SearchX} title={t('memorySearch.noResults')} />
          ) : (
            <ol className="data-list">
              {results.map((r, i) => (
                <li key={i} className="data-row">
                  <div className="mb-1 flex items-center justify-between gap-3">
                    <span className="text-meta tabular-nums">#{i + 1}</span>
                    <span className={`font-mono text-[11px] font-semibold tabular-nums ${scoreClass(r.similarity)}`}>
                      <span className="mr-1 font-sans font-normal text-gray-400">{t('memorySearch.similarity')}</span>
                      {(r.similarity * 100).toFixed(1)}%
                    </span>
                  </div>
                  <p className="max-w-prose text-[13px] leading-relaxed text-gray-200">{r.content}</p>
                  <MetadataView metadata={r.metadata} />
                </li>
              ))}
            </ol>
          )}
        </Panel>
      )}

      <StoreMemoryForm companyId={companyId} />
    </div>
  )
}

/** Adds a note to the company's memory (`POST /v1/memory/store`, Manager). */
function StoreMemoryForm({ companyId }: { companyId: number | null }) {
  const { t } = useTranslation()
  const store = useStoreMemory()
  const [note, setNote] = useState('')
  const [saved, setSaved] = useState(false)

  function save() {
    const content = note.trim()
    if (!content) return
    setSaved(false)
    store.mutate(
      { content, companyId, metadata: { source: 'memory_page' } },
      { onSuccess: () => { setNote(''); setSaved(true) } },
    )
  }

  return (
    <Panel title={t('memorySearch.storeTitle', { defaultValue: 'Add to memory' })} className="mt-4">
      <form onSubmit={e => { e.preventDefault(); save() }} className="space-y-2 p-3">
        <p className="max-w-prose text-xs text-gray-400">
          {t('memorySearch.storeHint', { defaultValue: 'Agents retrieve these notes as context for the company\'s tasks.' })}
        </p>
        <textarea
          value={note}
          onChange={e => { setNote(e.target.value); setSaved(false) }}
          maxLength={MAX_NOTE_LENGTH}
          rows={3}
          aria-label={t('memorySearch.storeLabel', { defaultValue: 'Note to remember' })}
          placeholder={t('memorySearch.storePlaceholder', { defaultValue: 'A decision, a convention or a fact the agents should know' })}
          className="input w-full"
        />
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={store.isPending || !note.trim()} className="btn btn-secondary btn-sm">
            {store.isPending ? <Spinner /> : <Save size={14} aria-hidden="true" />}
            {store.isPending
              ? t('memorySearch.storing', { defaultValue: 'Saving…' })
              : t('memorySearch.store', { defaultValue: 'Save to memory' })}
          </button>
          <span className="text-meta tabular-nums">{note.length}/{MAX_NOTE_LENGTH}</span>
          {saved && (
            <span role="status" className="text-xs text-emerald-300">
              {t('memorySearch.stored', { defaultValue: 'Saved to memory' })}
            </span>
          )}
        </div>
        {store.isError && (
          <div role="alert" className="alert-error flex flex-wrap items-center justify-between gap-2">
            <span>{t('memorySearch.storeError', { message: errorDetail(store.error), defaultValue: 'Could not save: {{message}}' })}</span>
            <button type="button" onClick={save} className="btn-tool">{t('common.retry')}</button>
          </div>
        )}
      </form>
    </Panel>
  )
}
