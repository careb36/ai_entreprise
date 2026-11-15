import { useTranslation } from 'react-i18next'
import { Boxes, Cpu, Plug } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import ConsoleProducers from '../components/ConsoleProducers'
import GitWorkflowPanel from '../components/GitWorkflowPanel'
import ModelChoices from '../components/ModelChoices'
import ModelConnections from '../components/ModelConnections'
import RoutingPolicy from '../components/RoutingPolicy'
import { useCurrentRole } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'
import { modelsApi } from '../services/models'

/**
 * The company's Models screen (issue #356): the providers its agents can
 * reach and the models they run. A Manager changes them; a Viewer reads them.
 */
export default function Models() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'

  const models = useQuery({
    queryKey: ['company-models', companyId],
    queryFn: () => modelsApi.companyModels(companyId!),
    enabled: companyId !== null,
  })
  const connections = models.data?.connections ?? []
  const modelCount = connections.reduce((n, c) => n + (c.models?.length ?? 0), 0)
  const validated = connections.filter(c => c.validation_ok === true).length

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
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <div className="card p-3 text-center">
              <Plug size={16} className="mx-auto mb-1 text-brand-400" />
              <div className="text-2xl font-bold text-white">{connections.length}</div>
              <div className="text-xs text-gray-400">{t('models.statConnections', { defaultValue: 'Connections' })}</div>
            </div>
            <div className="card p-3 text-center">
              <Cpu size={16} className="mx-auto mb-1 text-brand-400" />
              <div className="text-2xl font-bold text-white">{modelCount}</div>
              <div className="text-xs text-gray-400">{t('models.statModels', { defaultValue: 'Models' })}</div>
            </div>
            <div className="card p-3 text-center">
              <Boxes size={16} className="mx-auto mb-1 text-emerald-400" />
              <div className="text-2xl font-bold text-white">{validated}</div>
              <div className="text-xs text-gray-400">{t('models.statValidated', { defaultValue: 'Validated' })}</div>
            </div>
          </div>
          <ModelConnections companyId={companyId} canManage={canManage} />
          <ModelChoices companyId={companyId} canManage={canManage} />
          <RoutingPolicy companyId={companyId} canManage={canManage} />
          <GitWorkflowPanel key={companyId} companyId={companyId} canManage={canManage} />
          <ConsoleProducers companyId={companyId} canManage={canManage} />
        </>
      )}
    </div>
  )
}
