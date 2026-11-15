/**
 * The "Architecture" section of an approved specification: the architecture
 * it has (the approved one first, else the newest draft) with its status,
 * version and check, linking to its page. For a Manager, when there is none
 * or the latest is superseded, a button asks the Architect for a design and
 * one opens the editor to write it.
 */
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Loader2, Pencil, Sparkles } from 'lucide-react'
import { errorDetail } from '../services/api'
import { architecturePath, latestArchitecture, useArchitectures, useDraftArchitecture } from '../services/architectures'
import type { Specification } from '../services/specifications'
import { CheckSummary, StatusBadge } from './SpecificationBadges'

export function ArchitectureSummary({ specification, canWrite }: { specification: Specification; canWrite: boolean }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const approved = specification.status === 'approved'
  const query = useArchitectures(approved ? specification.id : null)
  const draft = useDraftArchitecture()
  if (!approved) return null
  const latest = query.data ? latestArchitecture(query.data.architectures) : null
  const offerNew = canWrite && query.isSuccess && (latest === null || latest.status === 'superseded')

  function askTheArchitect() {
    draft.mutate({ specification_id: specification.id }, { onSuccess: architecture => navigate(architecturePath(specification.id, architecture.id)) })
  }

  return (
    <section aria-labelledby="specification-architecture" className="card p-4 space-y-3">
      <h2 id="specification-architecture" className="text-sm font-semibold text-white">{t('architectures.title')}</h2>
      {query.isLoading && (
        <p role="status" className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={15} className="animate-spin" aria-hidden="true" /> {t('architectures.loading')}
        </p>
      )}
      {query.isError && (
        <div role="alert" className="alert-error flex flex-wrap items-center gap-2 text-sm">
          <span>{t('architectures.loadError', { message: errorDetail(query.error) })}</span>
          <button type="button" onClick={() => query.refetch()} className="underline">{t('common.retry')}</button>
        </div>
      )}
      {query.isSuccess && latest === null && (
        <p className="text-sm text-gray-500">{canWrite ? t('architectures.noneHint') : t('architectures.noneViewer')}</p>
      )}
      {latest && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
          <Link to={architecturePath(specification.id, latest.id)} className="text-sm font-semibold text-white hover:text-brand-300">{latest.title}</Link>
          <span>{t('specifications.version', { version: latest.version })}</span>
          <StatusBadge status={latest.status} />
          {latest.drafted_by && <span>{t('specifications.draftedBy', { who: latest.drafted_by })}</span>}
          <CheckSummary check={latest.check} />
        </p>
      )}
      {offerNew && (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={askTheArchitect} disabled={draft.isPending} className="btn btn-primary btn-sm">
            {draft.isPending ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <Sparkles size={14} aria-hidden="true" />}
            {draft.isPending ? t('architectures.drafting') : t('architectures.askArchitect')}
          </button>
          <button type="button" onClick={() => navigate(architecturePath(specification.id, 'new'))} disabled={draft.isPending} className="btn btn-secondary btn-sm">
            <Pencil size={14} aria-hidden="true" /> {t('architectures.writeYourself')}
          </button>
          {draft.isError && <span role="alert" className="text-sm text-red-300">{t('architectures.draftError', { message: errorDetail(draft.error) })}</span>}
        </div>
      )}
    </section>
  )
}
