import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useCalibration, type CalibrationNumbers, type CalibrationReport as Report } from '../services/calibration'
import type { Translate } from '../i18n/translate'
import { Panel, SegmentedFilter, TABLE, TD, TH, TR } from './ui'

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
    <div className="bg-surface-900 px-3 py-2">
      <div className="text-lg font-semibold tabular-nums text-gray-100">{value}</div>
      <div className="text-meta">{label}</div>
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
    <Panel title={title}>
      <p className="px-3 py-2 text-xs text-gray-400">{t('calibration.hint')}</p>
      <div className="grid grid-cols-2 gap-px border-y border-surface-700 bg-surface-700 sm:grid-cols-4">
        <Tile label={t('calibration.spearman')} value={overall.spearman === null ? '–' : overall.spearman.toFixed(2)} />
        <Tile label={t('calibration.offByTwo')} value={pct(overall.off_by_two_bands_share)} />
        <Tile label={t('calibration.lowReworked')} value={pct(low?.over_one_rework_share ?? null)} />
        <Tile label={t('calibration.tokensUnknown')} value={String(overall.tokens_unknown)} />
      </div>
      <div className="px-3 py-2">
        <SegmentedFilter label={t('calibration.splitLabel')} value={split} onChange={setSplit}
          options={SPLITS.map(name => ({ value: name, label: t(`calibration.by.${name}`) }))} />
      </div>
      <div className="overflow-x-auto">
        <table className={TABLE}>
          <caption className="sr-only">{t(`calibration.caption.${split}`)}</caption>
          <thead>
            <tr>
              <th scope="col" className={TH}>{t(`calibration.column.${split}`)}</th>
              <th scope="col" className={`${TH} text-right`}>{t('calibration.tasks')}</th>
              <th scope="col" className={`${TH} text-right`}>{t('calibration.overOneRework')}</th>
              <th scope="col" className={`${TH} text-right`}>{t('calibration.tokens')}</th>
              <th scope="col" className={`${TH} text-right`}>{t('calibration.offByTwoShort')}</th>
              <th scope="col" className={`${TH} text-right`}>{t('calibration.spearmanShort')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.name} className={TR}>
                <th scope="row" className={`${TD} font-normal`}>{row.name}</th>
                <td className={`${TD} text-right tabular-nums`}>{row.numbers.tasks}</td>
                <td className={`${TD} text-right tabular-nums`}>{pct(row.numbers.over_one_rework_share)}</td>
                <td className={`${TD} text-right tabular-nums`}>
                  {spreadText(row.numbers)}
                  {row.numbers.tokens_unknown > 0 && row.numbers.tokens_spread && (
                    <span className="text-gray-400"> {t('calibration.plusUnknown', { count: row.numbers.tokens_unknown })}</span>
                  )}
                </td>
                <td className={`${TD} text-right tabular-nums`}>{pct(row.numbers.off_by_two_bands_share)}</td>
                <td className={`${TD} text-right tabular-nums`}>{row.numbers.spearman === null ? '–' : row.numbers.spearman.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="space-y-1 border-t border-surface-700 px-3 py-2">
        {data.signals.length > 0 && (
          <ul aria-label={t('calibration.signalsLabel')} className="list-disc space-y-0.5 pl-5 text-xs text-amber-300">
            {data.signals.map(s => (
              <li key={s.signal}>{t(`calibration.signal.${s.signal}`, { value: s.signal === 'no_rank_agreement' ? s.value.toFixed(2) : s.signal === 'low_band_reworked' ? pct(s.value) : Math.round(s.value) })}</li>
            ))}
          </ul>
        )}
        <p className="text-xs text-gray-400">{t('calibration.informationOnly')}</p>
        {overall.tokens_unknown > 0 && (
          <p className="text-xs text-gray-400">{t('calibration.unknownNote', { count: overall.tokens_unknown })}</p>
        )}
        {data.predicted_recomputed > 0 && (
          <p className="text-xs text-gray-400">{t('calibration.recomputed', { count: data.predicted_recomputed })}</p>
        )}
      </div>
    </Panel>
  )
}
