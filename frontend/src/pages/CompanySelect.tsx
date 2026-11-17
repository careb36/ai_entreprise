import { useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Building2, ChevronRight } from 'lucide-react'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'
import { safeReturnPath } from '../lib/returnPath'
import { CountPill, EmptyState, PageHeader, Panel } from '../components/ui'

export default function CompanySelect() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const memberships = useAuthStore(state => state.memberships)
  const setCompany = useAppStore(state => state.setCompany)

  function selectCompany(companyId: number) {
    queryClient.removeQueries()
    setCompany(
      companyId,
      t('auth.companyPlaceholder', { id: companyId, defaultValue: 'Company {{id}}' }),
    )
    navigate(safeReturnPath(searchParams.get('from')), { replace: true })
  }

  return (
    <div className="page-container max-w-2xl">
      <PageHeader
        title={t('auth.selectCompany', { defaultValue: 'Select a company' })}
        subtitle={t('auth.selectCompanySubtitle')}
      />
      {memberships.length === 0 ? (
        <EmptyState icon={Building2} dashed title={t('auth.noCompanies')} />
      ) : (
        <Panel title={t('auth.yourCompanies')}
          count={<CountPill count={memberships.length} label={t('auth.companyCount', { count: memberships.length })} />}>
          <ul className="data-list">
            {memberships.map(({ company_id, human_role }) => (
              <li key={company_id}>
                <button
                  type="button"
                  onClick={() => selectCompany(company_id)}
                  className="data-row-link group flex w-full items-center gap-3 text-left"
                >
                  <Building2 size={15} aria-hidden="true" className="flex-shrink-0 text-gray-400" />
                  <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-gray-100">
                    {t('auth.companyPlaceholder', { id: company_id, defaultValue: 'Company {{id}}' })}
                  </span>
                  <span className="chip">{human_role}</span>
                  <ChevronRight size={14} aria-hidden="true" className="flex-shrink-0 text-gray-400 group-hover:text-brand-300" />
                </button>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  )
}
