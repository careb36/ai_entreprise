/**
 * The company's architecture, version by version (issue #329, F3): which
 * version this page shows, whether it is the current one, the earlier
 * specifications it also covers, and every version of the company's
 * architecture with the specification it was drafted for and when it was
 * approved. A company has one architecture; each approved revision is a new
 * version and the earlier ones stay readable.
 */
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { History, Loader2 } from 'lucide-react'
import { errorDetail } from '../services/api'
import { type Architecture, architecturePath, useCompanyArchitecture } from '../services/architectures'
import { StatusBadge } from './SpecificationBadges'

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
  } catch {
    return iso
  }
}

/** One line under the title: the company's version number, current or not, and what else it covers. */
export function ArchitectureVersionLine({ architecture }: { architecture: Architecture }) {
  const { t } = useTranslation()
  const earlier = architecture.earlier_specifications ?? []
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
      <span>{t('architectures.history.companyVersion', { version: architecture.version })}</span>
      {architecture.is_current && <span className="badge bg-emerald-900/60 text-emerald-300">{t('architectures.history.current')}</span>}
      {earlier.length > 0 && (
        <span>{t('architectures.history.alsoCovers', { titles: earlier.map(s => s.title).join(', ') })}</span>
      )}
    </p>
  )
}

export function ArchitectureHistory({ architecture }: { architecture: Architecture }) {
  const { t } = useTranslation()
  const query = useCompanyArchitecture(architecture.company_id)
  const versions = query.data?.versions ?? []
  const currentId = query.data?.current?.id ?? null
  return (
    <section aria-labelledby="architecture-history" className="card p-4 space-y-2">
      <h2 id="architecture-history" className="flex items-center gap-2 text-sm font-semibold text-white">
        <History size={15} aria-hidden="true" /> {t('architectures.history.title')}
      </h2>
      {query.isLoading && (
        <p className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={14} className="animate-spin" aria-hidden="true" /> {t('architectures.history.loading')}
        </p>
      )}
      {query.isError && (
        <p className="text-sm text-gray-500">{t('architectures.history.loadError', { message: errorDetail(query.error) })}</p>
      )}
      {!query.isLoading && !query.isError && versions.length === 0 && (
        <p className="text-sm text-gray-500">{t('architectures.history.empty')}</p>
      )}
      <ol className="space-y-1">
        {versions.map(version => (
          <li key={version.id} className="flex flex-wrap items-center gap-2 text-sm" aria-current={version.id === architecture.id ? 'page' : undefined}>
            {version.id === architecture.id ? (
              <span className="font-semibold text-white">{t('architectures.history.version', { version: version.version })}</span>
            ) : (
              <Link to={architecturePath(version.specification_id, version.id)} className="text-brand-300 hover:underline">
                {t('architectures.history.version', { version: version.version })}
              </Link>
            )}
            <StatusBadge status={version.status} />
            {version.id === currentId && <span className="badge bg-emerald-900/60 text-emerald-300">{t('architectures.history.current')}</span>}
            {version.specification_title && (
              <span className="text-gray-300">{t('architectures.history.forSpecification', { title: version.specification_title })}</span>
            )}
            {version.specification_ids.length > 1 && (
              <span className="text-xs text-gray-400">{t('architectures.history.covers', { count: version.specification_ids.length })}</span>
            )}
            {version.approved_at && (
              <span className="text-xs text-gray-400">{t('architectures.history.approvedOn', { date: formatDate(version.approved_at) })}</span>
            )}
          </li>
        ))}
      </ol>
    </section>
  )
}
