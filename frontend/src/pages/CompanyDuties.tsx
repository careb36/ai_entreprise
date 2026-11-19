import { useTranslation } from 'react-i18next'
import AccessReviewPanel from '../components/AccessReviewPanel'
import ProtectedPathsPanel from '../components/ProtectedPathsPanel'
import ServiceAccountsPanel from '../components/ServiceAccountsPanel'
import SodPolicyPanel from '../components/SodPolicyPanel'
import SodReportPanel from '../components/SodReportPanel'
import { useCurrentRole } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'
import { AccessNotice, PageHeader } from '../components/ui'

/**
 * Duties and access, for the company's Owners (issue #543): who asked for a
 * change does not approve it (the policy and its single-person exception),
 * the report of every approval, the periodic access review, the service
 * accounts, and whether the company's protected paths hold (it hosts the
 * factory itself). The backend enforces the rule at each approval point and audits
 * every change; this page shows and sets it.
 */
export default function CompanyDuties() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()

  return (
    <div className="page-container max-w-5xl">
      <PageHeader title={t('duties.title')} subtitle={t('duties.subtitle')} />
      {role !== 'Owner' ? (
        <AccessNotice>{t('duties.ownerOnly', { role: role ?? '—' })}</AccessNotice>
      ) : (
        <div className="space-y-4">
          <SodPolicyPanel companyId={companyId} />
          <ProtectedPathsPanel companyId={companyId} />
          <AccessReviewPanel companyId={companyId} />
          <SodReportPanel companyId={companyId} />
          <ServiceAccountsPanel companyId={companyId} />
        </div>
      )}
    </div>
  )
}
