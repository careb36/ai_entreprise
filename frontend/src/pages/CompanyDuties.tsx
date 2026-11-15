import { useTranslation } from 'react-i18next'
import { Scale } from 'lucide-react'
import AccessReviewPanel from '../components/AccessReviewPanel'
import ServiceAccountsPanel from '../components/ServiceAccountsPanel'
import SodPolicyPanel from '../components/SodPolicyPanel'
import SodReportPanel from '../components/SodReportPanel'
import { useCurrentRole } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'

/**
 * Duties and access, for the company's Owners (issue #543): who asked for a
 * change does not approve it (the policy and its single-person exception),
 * the report of every approval, the periodic access review and the service
 * accounts. The backend enforces the rule at each approval point and audits
 * every change; this page shows and sets it.
 */
export default function CompanyDuties() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()

  return (
    <div className="page-container max-w-5xl">
      <div className="page-header mb-6">
        <div>
          <div className="flex items-center gap-2">
            <Scale className="text-brand-400" size={22} />
            <h1 className="page-title">{t('duties.title')}</h1>
          </div>
          <p className="text-gray-400 mt-1">{t('duties.subtitle')}</p>
        </div>
      </div>
      {role !== 'Owner' ? (
        <div role="status" className="card p-4 text-sm text-gray-300">
          {t('duties.ownerOnly', { role: role ?? '—' })}
        </div>
      ) : (
        <div className="space-y-4">
          <SodPolicyPanel companyId={companyId} />
          <AccessReviewPanel companyId={companyId} />
          <SodReportPanel companyId={companyId} />
          <ServiceAccountsPanel companyId={companyId} />
        </div>
      )}
    </div>
  )
}
