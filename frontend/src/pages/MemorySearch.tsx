import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../services/api'
import { Search, Loader2, Database, ChevronDown, ChevronUp } from 'lucide-react'

interface MemoryResult {
  content: string
  metadata: unknown
  similarity: number
}

function MetadataView({ metadata }: { metadata: unknown }) {
  const [open, setOpen] = useState(false)
  if (!metadata) return null

  let text: string
  try {
    text = typeof metadata === 'string' ? metadata : JSON.stringify(metadata, null, 2)
  } catch {
    text = String(metadata)
  }

  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-300 transition-colors"
      >
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        metadata
      </button>
      {open && (
        <pre className="mt-1 text-xs text-gray-400 bg-surface-900 border border-surface-700 p-2 rounded overflow-auto max-h-40 font-mono">
          {text}
        </pre>
      )}
    </div>
  )
}

export default function MemorySearch() {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(10)
  const [results, setResults] = useState<MemoryResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [searched, setSearched] = useState(false)

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    try {
      const res = await api.get<{ results: MemoryResult[] }>('/v1/memory/search', {
        query: query.trim(),
        limit: String(limit)
      })
      setResults(res.results || [])
      setSearched(true)
    } catch (err: any) {
      setError(err.message || t('memorySearch.error'))
    } finally {
      setLoading(false)
    }
  }

  function scoreClass(s: number) {
    if (s >= 0.85) return 'text-status-success'
    if (s >= 0.65) return 'text-yellow-400'
    return 'text-gray-500'
  }

  return (
    <div className="page-container max-w-4xl">
      <div className="page-header mb-6">
        <div className="flex items-center gap-2">
          <Database className="text-brand-400" size={22} />
          <h1 className="page-title">{t('memorySearch.title')}</h1>
        </div>
        <p className="text-gray-400 mt-1">{t('memorySearch.subtitle')}</p>
      </div>

      <form onSubmit={handleSearch} className="mb-6">
        <div className="flex flex-col sm:flex-row gap-2">
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={t('memorySearch.queryPlaceholder')}
            className="input flex-1"
          />
          <input
            type="number"
            value={limit}
            onChange={e => setLimit(parseInt(e.target.value) || 10)}
            min={1} max={100}
            className="input w-24 flex-shrink-0"
            title={t('memorySearch.limit')}
            aria-label={t('memorySearch.limit')}
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="btn btn-primary flex-shrink-0"
          >
            {loading ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
            {loading ? t('memorySearch.searching') : t('memorySearch.search')}
          </button>
        </div>
      </form>

      {error && <div className="alert-error mb-4">{error}</div>}

      {!searched && !loading && (
        <div className="empty-state py-16">
          <Database size={32} className="mx-auto mb-3 text-gray-600" />
          <p className="text-gray-500">{t('memorySearch.query', 'Enter a query to search memory')}</p>
        </div>
      )}

      {loading && (
        <div className="space-y-3">
          {[1,2,3].map(i => <div key={i} className="skeleton h-20 rounded-lg" />)}
        </div>
      )}

      {searched && !loading && (
        <>
          <p className="text-sm text-gray-500 mb-4">
            {results.length === 0
              ? t('memorySearch.noResults')
              : `${results.length} ${t('memorySearch.results')}`}
          </p>
          <div className="space-y-3">
            {results.map((r, i) => (
              <div key={i} className="card p-4">
                <div className="flex items-start justify-between gap-3 mb-2">
                  <span className="text-xs text-gray-500">#{i + 1}</span>
                  <span className={`text-xs font-semibold font-mono ${scoreClass(r.similarity)}`}>
                    {(r.similarity * 100).toFixed(1)}%
                  </span>
                </div>
                <p className="text-gray-200 text-sm leading-relaxed">{r.content}</p>
                <MetadataView metadata={r.metadata} />
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
