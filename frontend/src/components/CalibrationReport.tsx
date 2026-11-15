import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Target } from 'lucide-react'
import { useCalibration, type CalibrationNumbers, type CalibrationReport as Report } from '../services/calibration'
import type { Translate } from '../i18n/translate'

type Split = 'band' | 'role' | 'producer' | 'model' | 'sprint'
const SPLITS: Split[] = ['band', 'role', 'producer', 'model', 'sprint']

function pct(value: number | null) {
  return value === null ? '–' : `${Math.round(value * 100)}%`
}

function groups(data: Report, split: Split, t: Translate) {
  const none = t('calibration.none')
  switch (split) {
    case 'band':
      return data.by_predicted_band.map(g => ({ name: t(`routing.split.bandName.${g.predicted_band}`), numbers: g }))
    case 'role':
      return data.by_role.map(g => ({ name: g.role ?? none, numbers: g }))
    case 'producer':
      return data.by_producer.map(g => ({ name: g.producer ?? none, numbers: g }))
    case 'model':
      return data.by_model.map(g => ({ name: g.model ?? none, numbers: g }))
    case 'sprint':
      return data.by_sprint_id.map(g => ({ name: g.sprint_id === null ? none : t('calibration.sprintName', { id: g.sprint_id }), numbers: g }))
  }
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-surface-700 p-2 text-center">
      <div className="text-lg font-bold text-white tabular-nums">{value}</div>
      <div className="text-xs text-gray-400">{label}</div>
    </div>
  )
}

/**
 * Whether the difficulty estimate predicts the work (issue #534): the score
 * stored before each task ran beside the effort the backend observed from
 * its records at sprint close. Information for the routing policy, never
 * applied to it; nothing until a sprint has closed.
 */
export function CalibrationReport({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const [split, setSplit] = useState<Split>('band')
  const report = useCalibration(companyId)
  const data = report.data
  if (!data || data.tasks === 0) return null
  const title = t('calibration.title')
  const overall = data.overall
  const low = data.by_predicted_band.find(g => g.predicted_band === 'low')
  const rows = groups(data, split, t)
  const spreadText = (n: CalibrationNumbers) =>
    n.tokens_spread
      ? t('calibration.spreadValue', { median: Math.round(n.tokens_spread.median), q1: Math.round(n.tokens_spread.q1), q3: Math.round(n.tokens_spread.q3) })
      : t('calibration.unknown')

  return (
    <section aria-label={title} className="card space-y-3 p-4 text-sm">
      <h2 className="flex items-center gap-1.5 font-semibold text-white">
        <Target size={14} aria-hidden="true" />
        {title}
      </h2>
      <p className="text-xs text-gray-500">{t('calibration.hint')}</p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Tile label={t('calibration.spearman')} value={overall.spearman === null ? '–' : overall.spearman.toFixed(2)} />
        <Tile label={t('calibration.offByTwo')} value={pct(overall.off_by_two_bands_share)} />
        <Tile label={t('calibration.lowReworked')} value={pct(low?.over_one_rework_share ?? null)} />
        <Tile label={t('calibration.tokensUnknown')} value={String(overall.tokens_unknown)} />
      </div>
      <div role="group" aria-label={t('calibration.splitLabel')} className="flex flex-wrap gap-1">
        {SPLITS.map(name => (
          <button key={name} type="button" aria-pressed={split === name} onClick={() => setSplit(name)}
            className={`btn-sm ${split === name ? 'btn-primary' : 'btn-ghost'}`}>
            {t(`calibration.by.${name}`)}
          </button>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs text-gray-300">
          <caption className="sr-only">{t(`calibration.caption.${split}`)}</caption>
          <thead className="text-gray-500">
            <tr>
              <th scope="col" className="py-1 pr-3 font-medium">{t(`calibration.column.${split}`)}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('calibration.tasks')}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('calibration.overOneRework')}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('calibration.tokens')}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('calibration.offByTwoShort')}</th>
              <th scope="col" className="py-1 text-right font-medium">{t('calibration.spearmanShort')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.name} className="border-t border-surface-700">
                <th scope="row" className="py-1 pr-3 font-normal">{row.name}</th>
                <td className="py-1 pr-3 text-right tabular-nums">{row.numbers.tasks}</td>
                <td className="py-1 pr-3 text-right tabular-nums">{pct(row.numbers.over_one_rework_share)}</td>
                <td className="py-1 pr-3 text-right tabular-nums">
                  {spreadText(row.numbers)}
                  {row.numbers.tokens_unknown > 0 && row.numbers.tokens_spread && (
                    <span className="text-gray-500"> {t('calibration.plusUnknown', { count: row.numbers.tokens_unknown })}</span>
                  )}
                </td>
                <td className="py-1 pr-3 text-right tabular-nums">{pct(row.numbers.off_by_two_bands_share)}</td>
                <td className="py-1 text-right tabular-nums">{row.numbers.spearman === null ? '–' : row.numbers.spearman.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.signals.length > 0 && (
        <ul aria-label={t('calibration.signalsLabel')} className="list-disc space-y-0.5 pl-5 text-xs text-amber-300">
          {data.signals.map(s => (
            <li key={s.signal}>{t(`calibration.signal.${s.signal}`, { value: s.signal === 'no_rank_agreement' ? s.value.toFixed(2) : s.signal === 'low_band_reworked' ? pct(s.value) : Math.round(s.value) })}</li>
          ))}
        </ul>
      )}
      <p className="text-xs text-gray-500">{t('calibration.informationOnly')}</p>
      {overall.tokens_unknown > 0 && (
        <p className="text-xs text-gray-500">{t('calibration.unknownNote', { count: overall.tokens_unknown })}</p>
      )}
      {data.predicted_recomputed > 0 && (
        <p className="text-xs text-gray-500">{t('calibration.recomputed', { count: data.predicted_recomputed })}</p>
      )}
    </section>
  )
}
