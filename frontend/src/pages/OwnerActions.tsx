import { useTranslation } from 'react-i18next'
import { Building2, Lock } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { useOwnerActions } from '../hooks/useOwnerActions'
import { errorDetail } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { EmptyState, LoadError, LoadingRows, PageHeader } from '../components/ui'
import { ActionCard } from './ownerActions/ActionCard'

/**
 * Owner actions (`/company/owner-actions`, issue #701): steps only the
 * company's Owner takes, run by the platform from a catalogue after a
 * confirmation that shows their cost and effect. One run of an action at a
 * time; each can be cancelled; start, cancel and finish are in the company's
 * audit chain. What a run recorded is what the platform observed.
 */
export default function OwnerActions() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const isOwner = useCurrentRole() === 'Owner'
  const query = useOwnerActions(isOwner ? companyId : null)

  return (
    <div className="page-container max-w-4xl space-y-4">
      <PageHeader title={t('ownerActions.title')} subtitle={t('ownerActions.subtitle')} />
      {companyId === null ? (
        <EmptyState icon={Building2} title={t('ownerActions.noCompany')} dashed />
      ) : !isOwner ? (
        <EmptyState icon={Lock} title={t('ownerActions.ownerOnly')} dashed />
      ) : query.isPending ? (
        <LoadingRows label={t('ownerActions.loading')} />
      ) : query.isError ? (
        <LoadError onRetry={() => query.refetch()}>{t('ownerActions.loadError', { message: errorDetail(query.error) })}</LoadError>
      ) : (
        query.data.actions.map(action => <ActionCard key={action.name} companyId={companyId} action={action} />)
      )}
    </div>
  )
}
