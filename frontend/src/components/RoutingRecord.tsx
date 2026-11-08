import { useTranslation } from 'react-i18next'
import { Route } from 'lucide-react'
import { useRoutingRecord } from '../services/routing'

function pct(value: number) {
  return `${Math.round(value * 100)}%`
}

/**
 * Whether choosing the model by the task pays (issue #319): per difficulty and
 * model, over the company's completed sprints, the tasks done at the first
 * attempt, the rework per task, the blocked share and the tokens the provider
 * reported. Counted from the boards and the audit log; nothing until a
 * sprint has closed.
 */
export function RoutingRecord({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const record = useRoutingRecord(companyId)
  const data = record.data
  if (!data || data.rows.length === 0) return null
  const title = t('routing.title', { defaultValue: 'Difficulty and model' })
  const unreported = data.rows.reduce((sum, row) => sum + row.tokens.turns_unreported, 0)

  return (
    <section aria-label={title} className="card space-y-2 p-4 text-sm">
      <h2 className="flex items-center gap-1.5 font-semibold text-white">
        <Route size={14} aria-hidden="true" />
        {title}
      </h2>
      <p className="text-xs text-gray-500">
        {t('routing.hint', {
          defaultValue: 'Closed sprints, by the difficulty each task was routed by and the model of its last delivery.',
        })}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs text-gray-300">
          <caption className="sr-only">{t('routing.caption', { defaultValue: 'Tasks by difficulty and model' })}</caption>
          <thead className="text-gray-500">
            <tr>
              <th scope="col" className="py-1 pr-3 font-medium">{t('routing.difficulty', { defaultValue: 'Difficulty' })}</th>
              <th scope="col" className="py-1 pr-3 font-medium">{t('routing.model', { defaultValue: 'Model' })}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('routing.tasks', { defaultValue: 'Tasks' })}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('routing.firstAttempt', { defaultValue: 'First attempt' })}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('routing.rework', { defaultValue: 'Rework / task' })}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('routing.blocked', { defaultValue: 'Blocked' })}</th>
              <th scope="col" className="py-1 text-right font-medium">{t('routing.tokens', { defaultValue: 'Reported tokens' })}</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map(row => (
              <tr key={`${row.difficulty}-${row.model ?? ''}`} className="border-t border-surface-700">
                <th scope="row" className="py-1 pr-3 font-normal tabular-nums">
                  {row.band
                    ? t('routing.difficultyBand', { difficulty: row.difficulty, band: row.band, defaultValue: '{{difficulty}} (band {{band}})' })
                    : row.difficulty}
                </th>
                <td className="py-1 pr-3 font-mono text-gray-200">
                  {row.model ?? t('routing.noDelivery', { defaultValue: 'no delivery' })}
                </td>
                <td className="py-1 pr-3 text-right tabular-nums">{row.tasks}</td>
                <td className="py-1 pr-3 text-right tabular-nums">
                  {t('routing.firstAttemptValue', { first: row.done_first_attempt, done: row.done, rate: pct(row.first_attempt_rate), defaultValue: '{{rate}} ({{first}}; {{done}} done)' })}
                </td>
                <td className="py-1 pr-3 text-right tabular-nums">{row.rework_per_task.toFixed(2)}</td>
                <td className="py-1 pr-3 text-right tabular-nums">{pct(row.blocked_share)}</td>
                <td className="py-1 text-right tabular-nums">{row.tokens.reported}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {unreported > 0 && (
        <p className="text-xs text-gray-500">
          {t('routing.unreported', { count: unreported, defaultValue: '{{count}} turns without a reported token count, left out of the tokens.' })}
        </p>
      )}
    </section>
  )
}
