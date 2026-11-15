import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatCostCents } from '../services/cost'
import type { RoutingNumbers, RoutingRecord } from '../services/routing'

type Split = 'band' | 'model' | 'policy'

function pct(value: number) {
  return `${Math.round(value * 100)}%`
}

/**
 * Done, rework and cost split by the estimate's difficulty band, by the model
 * the proxy served, or by the routing policy each task ran under (issue #319).
 * The cost is shown only for tasks whose every turn was priced; the others are
 * counted apart, never as 0.
 */
export function RoutingSplit({ data }: { data: RoutingRecord }) {
  const { t, i18n } = useTranslation()
  const [split, setSplit] = useState<Split>('band')
  const groups: Record<Split, { name: string; numbers: RoutingNumbers }[]> = {
    band: (data.by_difficulty_band ?? []).map(g => ({ name: t(`routing.split.bandName.${g.difficulty_band}`), numbers: g })),
    model: (data.by_model ?? []).map(g => ({ name: g.model ?? t('routing.noDelivery'), numbers: g })),
    policy: (data.by_policy ?? []).map(g => ({ name: g.policy ?? t('routing.split.noPolicy'), numbers: g })),
  }
  if (!groups.band.length && !groups.model.length) return null
  const splits: Split[] = groups.policy.some(g => g.name !== t('routing.split.noPolicy')) ? ['band', 'model', 'policy'] : ['band', 'model']
  const rows = groups[split]
  const caption = t(`routing.split.caption.${split}`)

  return (
    <div className="space-y-2">
      <div role="group" aria-label={t('routing.split.label')} className="flex flex-wrap gap-1">
        {splits.map(name => (
          <button key={name} type="button" aria-pressed={split === name} onClick={() => setSplit(name)}
            className={`btn-sm ${split === name ? 'btn-primary' : 'btn-ghost'}`}>
            {t(`routing.split.by.${name}`)}
          </button>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs text-gray-300">
          <caption className="sr-only">{caption}</caption>
          <thead className="text-gray-500">
            <tr>
              <th scope="col" className="py-1 pr-3 font-medium">{t(`routing.split.column.${split}`)}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('routing.tasks')}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('routing.split.done')}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('routing.firstAttempt')}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('routing.rework')}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('routing.blocked')}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('routing.tokens')}</th>
              <th scope="col" className="py-1 text-right font-medium">{t('routing.split.cost')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ name, numbers: n }) => (
              <tr key={name} className="border-t border-surface-700">
                <th scope="row" className="py-1 pr-3 font-normal">{name}</th>
                <td className="py-1 pr-3 text-right tabular-nums">{n.tasks}</td>
                <td className="py-1 pr-3 text-right tabular-nums">{n.done}</td>
                <td className="py-1 pr-3 text-right tabular-nums">{pct(n.first_attempt_rate)}</td>
                <td className="py-1 pr-3 text-right tabular-nums">{n.rework_per_task.toFixed(2)}</td>
                <td className="py-1 pr-3 text-right tabular-nums">{pct(n.blocked_share)}</td>
                <td className="py-1 pr-3 text-right tabular-nums">{n.tokens.reported}</td>
                <td className="py-1 text-right tabular-nums">
                  {n.cost_cents == null
                    ? t('routing.split.costUnknown')
                    : formatCostCents(n.cost_cents, i18n.language)}
                  {n.cost_cents != null && (n.cost_unknown ?? 0) > 0 && (
                    <span className="text-gray-500"> {t('routing.split.costUnpriced', { count: n.cost_unknown })}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
