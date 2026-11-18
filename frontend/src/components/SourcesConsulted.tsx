/**
 * The sources an agent consulted while drafting (the research tools): each
 * one an observation the backend made — the URL, when it was fetched, the
 * HTTP status and the hash of what came back — or a refusal, when the host
 * was not on the company's list. What the agent concluded from a source is
 * its claim; this list is the record.
 */
import { useTranslation } from 'react-i18next'
import { BookOpenCheck, CircleX, ShieldAlert } from 'lucide-react'
import type { Source } from '../services/specifications'
import { Panel } from './ui'

export type { Source }

function formatDate(iso: string) {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

export function SourcesConsulted({ sources, headingId }: { sources: Source[]; headingId: string }) {
  const { t } = useTranslation()
  if (sources.length === 0) return null
  return (
    <Panel title={<span id={headingId} className="inline-flex items-center gap-2"><BookOpenCheck size={14} aria-hidden="true" />{t('sources.title', { count: sources.length })}</span>}>
      <p className="text-meta px-3 pt-2">{t('sources.hint')}</p>
      <ul className="data-list text-xs">
        {sources.map((source, index) => {
          const refused = source.allowed === false
          const label = source.package
            ? `${source.ecosystem ?? ''}:${source.package}${source.version ? `@${source.version}` : ''}`
            : source.url ?? source.host ?? ''
          return (
            <li key={`${source.url ?? label}-${index}`} className="data-row flex flex-wrap items-center gap-x-2 gap-y-1 py-2 text-gray-300">
              <span className="chip font-mono">{source.tool}</span>
              {source.url && !refused ? (
                <a href={source.url} target="_blank" rel="noreferrer" className="min-w-0 break-all text-brand-300 hover:underline">{label}</a>
              ) : (
                <span className="min-w-0 break-all">{label}</span>
              )}
              {refused
                ? <span className="inline-flex items-center gap-1 text-amber-300"><ShieldAlert size={12} aria-hidden="true" />{t('sources.refused', { host: source.host ?? '' })}</span>
                : source.is_error
                  ? <span className="inline-flex items-center gap-1 text-red-300"><CircleX size={12} aria-hidden="true" />{t('sources.failed', { status: source.status ?? '' })}</span>
                  : (
                    <span className="text-meta tabular-nums">
                      {t('sources.fetched', { status: source.status ?? '', hash: (source.sha256 ?? '').slice(0, 12), date: source.fetched_at ? formatDate(source.fetched_at) : '' })}
                    </span>
                  )}
            </li>
          )
        })}
      </ul>
    </Panel>
  )
}
