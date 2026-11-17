import { useTranslation } from 'react-i18next'
import { Boxes, CircleCheck, Cpu, Plug } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { CalibrationReport } from '../components/CalibrationReport'
import ConsoleProducers from '../components/ConsoleProducers'
import GitWorkflowPanel from '../components/GitWorkflowPanel'
import ModelChoices from '../components/ModelChoices'
import ModelConnections from '../components/ModelConnections'
import RoutingPolicy from '../components/RoutingPolicy'
import { useCurrentRole } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'
import { modelsApi } from '../services/models'
import { EmptyState, PageHeader } from '../components/ui'

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
    <div className="page-container max-w-5xl space-y-4">
      <PageHeader title={t('models.title')} subtitle={t('models.subtitle')} />
      {companyId === null ? (
        <EmptyState icon={Boxes} title={t('models.noCompany')} dashed />
      ) : (
        <>
          <dl className="panel grid grid-cols-3 divide-x divide-surface-700">
            {[
              { icon: Plug, value: connections.length, label: t('models.statConnections', { defaultValue: 'Connections' }) },
              { icon: Cpu, value: modelCount, label: t('models.statModels', { defaultValue: 'Models' }) },
              { icon: CircleCheck, value: validated, label: t('models.statValidated', { defaultValue: 'Validated' }) },
            ].map(({ icon: Icon, value, label }) => (
              <div key={label} className="px-3 py-2.5">
                <dt className="text-meta flex items-center gap-1"><Icon size={11} aria-hidden="true" />{label}</dt>
                <dd className="mt-0.5 text-xl font-semibold tabular-nums text-gray-100">{value}</dd>
              </div>
            ))}
          </dl>
          <ModelConnections companyId={companyId} canManage={canManage} />
          <ModelChoices companyId={companyId} canManage={canManage} />
          <RoutingPolicy companyId={companyId} canManage={canManage} />
          <CalibrationReport companyId={companyId} />
          <GitWorkflowPanel key={companyId} companyId={companyId} canManage={canManage} />
          <ConsoleProducers companyId={companyId} canManage={canManage} />
        </>
      )}
    </div>
  )
}
