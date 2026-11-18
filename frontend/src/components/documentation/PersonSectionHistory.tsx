/**
 * Every revision of one person section, newest first, withdrawals included:
 * who wrote it, in which role, when, and which text. Loaded only when opened.
 */
import { useTranslation } from 'react-i18next'
import { LoadErrorAlert, LoadingLine } from '../knowledge/PageStates'
import { errorDetail } from '../../services/api'
import { usePersonSectionHistory } from '../../services/documentation'
import { StatementClaim } from './StatementClaim'

export function PersonSectionHistory({ companyId, section }: { companyId: number; section: string }) {
  const { t } = useTranslation()
  const history = usePersonSectionHistory(companyId, section)
  if (history.isLoading) return <LoadingLine>{t('documentation.personSections.historyLoading')}</LoadingLine>
  if (history.isError) {
    return (
      <LoadErrorAlert onRetry={() => history.refetch()}>
        {t('documentation.personSections.historyError', { message: errorDetail(history.error) })}
      </LoadErrorAlert>
    )
  }
  const revisions = history.data?.revisions ?? []
  if (revisions.length === 0) return <p className="text-xs text-gray-400">{t('documentation.personSections.historyEmpty')}</p>
  return (
    <ol className="space-y-2" aria-label={t('documentation.personSections.historyTitle')}>
      {revisions.map(revision => <li key={revision.id}><StatementClaim statement={revision} /></li>)}
    </ol>
  )
}
