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
import { CircleCheck, History } from 'lucide-react'
import { errorDetail } from '../services/api'
import { type Architecture, architecturePath, useCompanyArchitecture } from '../services/architectures'
import { StatusBadge } from './SpecificationBadges'
import { CountPill, Panel, Spinner, StatusBadge as Badge } from './ui'

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
    <span className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
      <span className="tabular-nums">{t('architectures.history.companyVersion', { version: architecture.version })}</span>
      {architecture.is_current && <Badge icon={CircleCheck} tone="success" label={t('architectures.history.current')} />}
      {earlier.length > 0 && (
        <span>{t('architectures.history.alsoCovers', { titles: earlier.map(s => s.title).join(', ') })}</span>
      )}
    </span>
  )
}

export function ArchitectureHistory({ architecture }: { architecture: Architecture }) {
  const { t } = useTranslation()
  const query = useCompanyArchitecture(architecture.company_id)
  const versions = query.data?.versions ?? []
  const currentId = query.data?.current?.id ?? null
  return (
    <Panel
      title={<span className="inline-flex items-center gap-2"><History size={14} aria-hidden="true" />{t('architectures.history.title')}</span>}
      count={versions.length > 0 ? <CountPill count={versions.length} label={t('architectures.history.countLabel', { count: versions.length })} /> : undefined}>
      {query.isLoading && (
        <p className="flex items-center gap-2 px-3 py-2.5 text-[13px] text-gray-400"><Spinner /> {t('architectures.history.loading')}</p>
      )}
      {query.isError && (
        <p className="px-3 py-2.5 text-[13px] text-gray-400">{t('architectures.history.loadError', { message: errorDetail(query.error) })}</p>
      )}
      {!query.isLoading && !query.isError && versions.length === 0 && (
        <p className="px-3 py-2.5 text-[13px] text-gray-400">{t('architectures.history.empty')}</p>
      )}
      <ol className="data-list">
        {versions.map(version => (
          <li key={version.id} className="data-row flex flex-wrap items-center gap-x-2 gap-y-1 py-2 text-[13px]" aria-current={version.id === architecture.id ? 'page' : undefined}>
            {version.id === architecture.id ? (
              <span className="font-semibold tabular-nums text-white">{t('architectures.history.version', { version: version.version })}</span>
            ) : (
              <Link to={architecturePath(version.specification_id, version.id)} className="tabular-nums text-brand-300 hover:underline">
                {t('architectures.history.version', { version: version.version })}
              </Link>
            )}
            <StatusBadge status={version.status} />
            {version.id === currentId && <Badge icon={CircleCheck} tone="success" label={t('architectures.history.current')} />}
            {version.specification_title && (
              <span className="text-gray-300">{t('architectures.history.forSpecification', { title: version.specification_title })}</span>
            )}
            {version.specification_ids.length > 1 && (
              <span className="text-meta">{t('architectures.history.covers', { count: version.specification_ids.length })}</span>
            )}
            {version.approved_at && (
              <span className="text-meta tabular-nums">{t('architectures.history.approvedOn', { date: formatDate(version.approved_at) })}</span>
            )}
          </li>
        ))}
      </ol>
    </Panel>
  )
}
