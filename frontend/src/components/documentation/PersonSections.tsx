/**
 * The person sections of one pack document (issue #530, phase 2): the
 * sections only a person can write, each with its current statement as a
 * claim and its history. Shown under a document that has any (the charter,
 * the risk register, the check results); renders nothing for the others.
 * `canWrite` is an Owner or a Manager, the role the backend requires to write
 * or withdraw; a Viewer reads.
 */
import { useTranslation } from 'react-i18next'
import { MessageSquareQuote } from 'lucide-react'
import { CountPill, Panel } from '../ui'
import { LoadErrorAlert, LoadingLine } from '../knowledge/PageStates'
import { errorDetail } from '../../services/api'
import { usePersonSections } from '../../services/documentation'
import { PersonSectionRow } from './PersonSectionRow'

export function PersonSections({ companyId, document, canWrite }: { companyId: number; document: string; canWrite: boolean }) {
  const { t } = useTranslation()
  const query = usePersonSections(companyId)
  if (query.isLoading) return <LoadingLine>{t('documentation.personSections.loading')}</LoadingLine>
  if (query.isError) {
    return (
      <LoadErrorAlert onRetry={() => query.refetch()}>
        {t('documentation.personSections.loadError', { message: errorDetail(query.error) })}
      </LoadErrorAlert>
    )
  }
  const sections = (query.data?.sections ?? []).filter(s => s.document === document)
  if (sections.length === 0) return null
  const filled = sections.filter(s => s.statement !== null).length
  return (
    <Panel
      title={<span className="inline-flex items-center gap-1.5"><MessageSquareQuote size={13} aria-hidden="true" className="text-gray-400" />{t('documentation.personSections.title')}</span>}
      count={<CountPill count={filled} total={sections.length} label={t('documentation.personSections.filled', { count: filled, total: sections.length })} />}
      className="border-dashed border-surface-600 bg-transparent"
    >
      <p className="border-b border-surface-700 px-4 py-2 text-xs text-gray-400">
        {t('documentation.personSections.intro')}{!canWrite && <> {t('documentation.personSections.readOnly')}</>}
      </p>
      <ul className="data-list">
        {sections.map(section => (
          <PersonSectionRow key={section.key} companyId={companyId} section={section} canWrite={canWrite}
            textLimit={query.data?.text_limit ?? 2000} />
        ))}
      </ul>
    </Panel>
  )
}
