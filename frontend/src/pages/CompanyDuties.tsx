import { useSearchParams } from 'react-router-dom'
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
 * every change; this page shows and sets it. With ``?company=N`` (the link
 * an owner action shows when the rule would refuse its approvals) it shows
 * only company N's policy (the backend lets only an Owner of N read it).
 */
export default function CompanyDuties() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const [search] = useSearchParams()
  const asked = Number(search.get('company')) || null
  const other = asked !== null && asked !== companyId ? asked : null

  if (other !== null) {
    return (
      <div className="page-container max-w-5xl">
        <PageHeader title={t('duties.title')} subtitle={t('duties.otherCompany', { company: other })} />
        {/* The backend decides: only an Owner of that company reads or sets its policy. */}
        <SodPolicyPanel companyId={other} />
      </div>
    )
  }

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
