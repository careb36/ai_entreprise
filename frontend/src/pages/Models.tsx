import { useTranslation } from 'react-i18next'
import { Boxes } from 'lucide-react'
import ModelChoices from '../components/ModelChoices'
import ModelConnections from '../components/ModelConnections'
import { useCurrentRole } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'

/**
 * The company's Models screen (issue #356): the providers its agents can
 * reach and the models they run. A Manager changes them; a Viewer reads them.
 */
export default function Models() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'

  return (
    <div className="page-container max-w-4xl space-y-6">
      <div className="page-header">
        <div>
          <div className="flex items-center gap-2">
            <Boxes className="text-brand-400" size={22} />
            <h1 className="page-title">{t('models.title')}</h1>
          </div>
          <p className="text-gray-400 mt-1">{t('models.subtitle')}</p>
        </div>
      </div>
      {companyId === null ? (
        <p className="text-sm text-gray-400">{t('models.noCompany')}</p>
      ) : (
        <>
          <ModelConnections companyId={companyId} canManage={canManage} />
          <ModelChoices companyId={companyId} canManage={canManage} />
        </>
      )}
    </div>
  )
}
