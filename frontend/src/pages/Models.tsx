import { useTranslation } from 'react-i18next'
import { useAppStore } from '../store/useAppStore'
import { providersApi } from '../services/providers'
import { useQuery } from '@tanstack/react-query'

export function Models() {
  const { t } = useTranslation()
  const companyId = useAppStore((s) => s.companyId)
  const { data: modelsData } = useQuery({
    queryKey: ['company-models', companyId],
    queryFn: () => providersApi.models(companyId!),
    enabled: companyId !== null,
  })
  const { data: suggestions } = useQuery({
    queryKey: ['company-model-suggestions', companyId],
    queryFn: () => providersApi.suggestions(companyId!),
    enabled: companyId !== null,
  })

  return (
    <div className="page-container">
      <h1 className="text-2xl font-semibold mb-4">{t('models.title')}</h1>
      <p className="text-sm text-gray-400 mb-6">{t('models.subtitle')}</p>
      {suggestions?.suggestions && (
        <div className="mb-6">
          <h2 className="text-lg font-medium mb-2">{t('models.suggestions')}</h2>
          <ul className="text-sm space-y-1">
            {Object.entries(suggestions.suggestions).map(([role, model]) => (
              <li key={role}>
                <span className="font-medium">{role}:</span> {model ?? t('models.none')}
              </li>
            ))}
          </ul>
        </div>
      )}
      {modelsData?.connections?.map((c) => (
        <div key={c.connection_id} className="mb-4 border border-gray-700 rounded p-3">
          <div className="font-medium">{c.name}</div>
          <div className="text-xs text-gray-400">{c.provider}</div>
          <div className="text-xs text-gray-500 mt-1">
            {c.models.length} {t('models.modelsCount')}
          </div>
        </div>
      ))}
    </div>
  )
}

export default Models