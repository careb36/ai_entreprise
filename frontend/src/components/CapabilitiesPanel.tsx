import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../services/api'
import type { CapabilityState } from '../services/api/system'
import { Panel } from './ui'

const STATE_CLASS: Record<CapabilityState['state'], string> = {
  on: 'text-emerald-300',
  degraded: 'text-amber-300',
  off: 'text-gray-400',
}

/**
 * What this deployment can do with the configuration it has (issue #751): each
 * capability on, off or degraded, with the reason the backend or the LLM proxy
 * gives. Read from `/health/deep`; off by configuration is not a fault.
 */
export default function CapabilitiesPanel() {
  const { t } = useTranslation()
  const deep = useQuery({
    queryKey: ['health', 'deep'],
    queryFn: () => api.deepHealth(),
    staleTime: 60_000,
    retry: false,
  })
  const entries = Object.entries(deep.data?.capabilities ?? {}).sort(([a], [b]) => a.localeCompare(b))

  return (
    <Panel title={t('capabilities.title')}>
      <div className="p-3 text-xs">
        <p className="mb-2 text-gray-400">{t('capabilities.hint')}</p>
        {deep.isLoading ? (
          <p role="status" className="text-gray-400">{t('capabilities.loading')}</p>
        ) : deep.isError || entries.length === 0 ? (
          <p role="alert" className="text-gray-400">{t('capabilities.unavailable')}</p>
        ) : (
          <table className="w-full table-fixed">
            <thead>
              <tr className="text-left text-gray-500">
                <th scope="col" className="w-1/4 py-1 font-medium">{t('capabilities.name')}</th>
                <th scope="col" className="w-24 py-1 font-medium">{t('capabilities.stateColumn')}</th>
                <th scope="col" className="py-1 font-medium">{t('capabilities.reason')}</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(([name, cap]) => (
                <tr key={name} className="border-t border-surface-700 align-top">
                  <td className="break-words py-1 pr-2 font-mono text-gray-200">{name}</td>
                  <td className={`py-1 pr-2 ${STATE_CLASS[cap.state] ?? 'text-gray-400'}`}>
                    {t(`capabilities.state.${cap.state}`, { defaultValue: cap.state })}
                  </td>
                  <td className="break-words py-1 text-gray-400">{cap.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  )
}
