/**
 * The sources an agent consulted while drafting (the research tools): each
 * one an observation the backend made — the URL, when it was fetched, the
 * HTTP status and the hash of what came back — or a refusal, when the host
 * was not on the company's list. What the agent concluded from a source is
 * its claim; this list is the record.
 */
import { useTranslation } from 'react-i18next'
import { BookOpenCheck, ShieldAlert } from 'lucide-react'

export interface Source {
  tool: string
  url?: string | null
  host?: string | null
  allowed?: boolean | null
  fetched_at?: string | null
  status?: number | null
  sha256?: string | null
  bytes?: number | null
  package?: string | null
  ecosystem?: string | null
  version?: string | null
  is_error?: boolean | null
}

function formatDate(iso: string) {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

export function SourcesConsulted({ sources, headingId }: { sources: Source[]; headingId: string }) {
  const { t } = useTranslation()
  if (sources.length === 0) return null
  return (
    <section aria-labelledby={headingId} className="card p-4 space-y-2">
      <h2 id={headingId} className="flex items-center gap-2 text-sm font-semibold text-white">
        <BookOpenCheck size={16} aria-hidden="true" />
        {t('sources.title', { count: sources.length })}
      </h2>
      <p className="text-xs text-gray-500">{t('sources.hint')}</p>
      <ul className="space-y-1 text-xs">
        {sources.map((source, index) => {
          const refused = source.allowed === false
          const label = source.package
            ? `${source.ecosystem ?? ''}:${source.package}${source.version ? `@${source.version}` : ''}`
            : source.url ?? source.host ?? ''
          return (
            <li key={`${source.url ?? label}-${index}`} className="flex flex-wrap items-center gap-2 text-gray-300">
              {refused ? <ShieldAlert size={12} aria-hidden="true" className="text-amber-300" /> : null}
              <span className="font-mono text-gray-400">{source.tool}</span>
              {source.url && !refused ? (
                <a href={source.url} target="_blank" rel="noreferrer" className="break-all text-brand-300 hover:underline">{label}</a>
              ) : (
                <span className="break-all">{label}</span>
              )}
              {refused
                ? <span className="text-amber-300">{t('sources.refused', { host: source.host ?? '' })}</span>
                : source.is_error
                  ? <span className="text-red-300">{t('sources.failed', { status: source.status ?? '' })}</span>
                  : (
                    <span className="text-gray-500">
                      {t('sources.fetched', { status: source.status ?? '', hash: (source.sha256 ?? '').slice(0, 12), date: source.fetched_at ? formatDate(source.fetched_at) : '' })}
                    </span>
                  )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
