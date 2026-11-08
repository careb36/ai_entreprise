import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useAppStore } from '../store/useAppStore'
import { connectionModelOptions, modelsApi, type ModelOption } from '../services/models'

/**
 * The models an agent may run through the company's own provider connections
 * (`conn:<id>:<model>`): only connections validated with success, only what
 * their provider listed. Empty without a company or while loading.
 */
export function useConnectionModelOptions(): ModelOption[] {
  const companyId = useAppStore(s => s.companyId)
  const { data } = useQuery({
    queryKey: ['company-models', companyId],
    queryFn: () => modelsApi.companyModels(companyId!),
    enabled: companyId !== null,
  })
  return data ? connectionModelOptions(data.connections) : []
}

/** One `<optgroup>` per connection, to place inside a model `<select>`. */
export function ConnectionModelOptgroups({ options }: { options: ModelOption[] }) {
  const { t } = useTranslation()
  const connections = [...new Set(options.map(o => o.connection))]
  return (
    <>
      {connections.map(connection => (
        <optgroup key={connection} label={t('agentsConfig.connectionGroup', { connection })}>
          {options.filter(o => o.connection === connection).map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </optgroup>
      ))}
    </>
  )
}
