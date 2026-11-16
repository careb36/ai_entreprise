import { useTranslation } from 'react-i18next'
import { useProtectedPaths, useSetProtectedPaths } from '../hooks/useDuties'
import { errorDetail } from '../services/api'

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
    <section className="card p-4 space-y-3" aria-label={t('duties.protectedPaths.title')}>
      <h2 className="text-base font-semibold text-gray-100">{t('duties.protectedPaths.title')}</h2>
      <p className="text-xs text-gray-400">{t('duties.protectedPaths.hint')}</p>
      {change.isError && <div role="alert" className="alert-error text-sm">{errorDetail(change.error)}</div>}
      <label className="flex items-center gap-2 text-sm text-gray-200">
        <input type="checkbox" checked={enabled} disabled={change.isPending}
          onChange={e => change.mutate(e.target.checked)} />
        {t('duties.protectedPaths.label')}
      </label>
      <details>
        <summary className="text-xs text-gray-300 cursor-pointer">
          {t('duties.protectedPaths.list', { count: patterns.length })}
        </summary>
        <ul className="mt-2 space-y-1 text-xs text-gray-400">
          {patterns.map(p => (
            <li key={p.pattern}><code className="text-gray-200">{p.pattern}</code> — {p.why}</li>
          ))}
        </ul>
      </details>
    </section>
  )
}
