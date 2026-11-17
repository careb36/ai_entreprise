import { useTranslation } from 'react-i18next'
import { useProtectedPaths, useSetProtectedPaths } from '../hooks/useDuties'
import { errorDetail } from '../services/api'
import { Panel } from './ui'

/**
 * Whether the company hosts the factory itself (`docs/design/rsi-loop.md`,
 * section 3.2): with it on, a delivery that changes a protected path (the
 * graders, the evidence, CI, the rules) waits for a person and no Manager
 * rule accepts it. The list is the running backend's, shown read-only; only
 * a signed-in Owner turns it on or off, and each change is audited.
 */
export default function ProtectedPathsPanel({ companyId }: { companyId: number | null }) {
  const { t } = useTranslation()
  const setting = useProtectedPaths(companyId, true)
  const change = useSetProtectedPaths(companyId)

  if (setting.isError) return <div role="alert" className="alert-error text-sm">{errorDetail(setting.error)}</div>
  if (!setting.data) return null
  const { enabled, patterns } = setting.data
  return (
    <Panel title={t('duties.protectedPaths.title')}>
      <div className="space-y-3 p-3">
        <p className="text-xs text-gray-400">{t('duties.protectedPaths.hint')}</p>
        {change.isError && <div role="alert" className="alert-error text-sm">{errorDetail(change.error)}</div>}
        <label className="flex items-center gap-2 text-[13px] text-gray-200">
          <input type="checkbox" className="h-4 w-4 flex-shrink-0 rounded accent-brand-500" checked={enabled} disabled={change.isPending}
            onChange={e => change.mutate(e.target.checked)} />
          {t('duties.protectedPaths.label')}
        </label>
        <details>
          <summary className="cursor-pointer rounded-sm text-xs text-gray-300 hover:text-white">
            {t('duties.protectedPaths.list', { count: patterns.length })}
          </summary>
          <ul className="mt-2 space-y-1 text-xs text-gray-400">
            {patterns.map(p => (
              <li key={p.pattern}><code className="font-mono text-gray-200">{p.pattern}</code> — {p.why}</li>
            ))}
          </ul>
        </details>
      </div>
    </Panel>
  )
}
