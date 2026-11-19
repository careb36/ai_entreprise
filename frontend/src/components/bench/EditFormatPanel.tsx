import { useTranslation } from 'react-i18next'
import { Ban, CheckCircle2, XCircle } from 'lucide-react'
import { Panel, StatusBadge, TABLE, TD, TH, TR } from '../ui'
import { MeasureValue } from './MeasureValue'
import { formatCount, formatShare } from '../../lib/benchFormat'
import type { BenchEditFormatRow, BenchEditFormats } from '../../services/api'

const LOOKS = {
  pass: { icon: CheckCircle2, tone: 'success' },
  fail: { icon: XCircle, tone: 'danger' },
  unusable: { icon: Ban, tone: 'warning' },
} as const

/** A row's outcome: pass, fail or unusable (its own badge, never a pass), or "Not measured" with the card's reason. */
function Outcome({ row }: { row: BenchEditFormatRow }) {
  const { t } = useTranslation()
  const { outcome } = row
  if (!outcome.measured) {
    return (
      <>
        <MeasureValue measure={outcome} format={String} />
        {typeof outcome.detail === 'string' && <span className="ml-1 text-meta">{outcome.detail}</span>}
      </>
    )
  }
  const look = LOOKS[outcome.value]
  return (
    <>
      <StatusBadge icon={look.icon} tone={look.tone} label={t(`bench.editFormats.outcomes.${outcome.value}`)} />
      {outcome.value !== 'pass' && row.reason && <span className="ml-1 text-meta">{row.reason}</span>}
    </>
  )
}

/**
 * The latest card's edit format per model (schema v7): the format the run
 * asked for, its deliveries, and one row per model and format with its
 * outcome. An unusable format is never shown as a pass; a row the run could
 * not measure and a share with no delivery observed say so, never a 0.
 */
export function EditFormatPanel({ data }: { data: BenchEditFormats }) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language
  const count = (v: number) => formatCount(v, locale)
  const share = (v: number) => formatShare(v, locale)
  const ratio = (v: { passed: number; total: number }) =>
    t('bench.editFormats.ratio', { passed: count(v.passed), total: count(v.total) })
  const { deliveries } = data
  return (
    <Panel title={t('bench.editFormats.title')}>
      <div className="space-y-2 p-3 text-xs">
        <p className="text-gray-400">{t('bench.editFormats.note')}</p>
        {data.requested && <p className="text-gray-300">{t('bench.editFormats.requested', { format: data.requested })}</p>}
        {deliveries && (
          <p className="text-gray-300">
            {t('bench.editFormats.deliveries', {
              wellFormed: count(deliveries.well_formed),
              observed: count(deliveries.observed),
              missing: count(deliveries.missing),
            })}{' '}
            <span className="text-gray-400">{t('bench.editFormats.wellFormedShare')}: </span>
            <MeasureValue measure={deliveries.well_formed_rate} format={share} />
          </p>
        )}
      </div>
      {data.rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className={TABLE}>
            <caption className="sr-only">{t('bench.editFormats.caption')}</caption>
            <thead>
              <tr>
                <th scope="col" className={TH}>{t('bench.editFormats.model')}</th>
                <th scope="col" className={TH}>{t('bench.editFormats.format')}</th>
                <th scope="col" className={TH}>{t('bench.editFormats.outcome')}</th>
                <th scope="col" className={TH}>{t('bench.editFormats.criteria')}</th>
                <th scope="col" className={TH}>{t('bench.editFormats.wellFormed')}</th>
                <th scope="col" className={TH}>{t('bench.editFormats.malformed')}</th>
                <th scope="col" className={TH}>{t('bench.editFormats.offFormat')}</th>
                <th scope="col" className={TH}>{t('bench.editFormats.refused')}</th>
                <th scope="col" className={TH}>{t('bench.editFormats.rework')}</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map(r => (
                <tr key={`${r.model}/${r.format}`} className={TR}>
                  <th scope="row" className={`${TD} font-medium`}>{r.model}</th>
                  <td className={TD}><code>{r.format}</code></td>
                  <td className={TD}><Outcome row={r} /></td>
                  <td className={TD}><MeasureValue measure={r.criteria} format={ratio} /></td>
                  <td className={`${TD} tabular-nums`}>
                    {ratio({ passed: r.well_formed, total: r.observed })}{' '}
                    <MeasureValue measure={r.well_formed_rate} format={v => `(${share(v)})`} />
                  </td>
                  <td className={`${TD} tabular-nums`}>{count(r.malformed_edit_blocks)}</td>
                  <td className={`${TD} tabular-nums`}>{count(r.off_format)}</td>
                  <td className={`${TD} tabular-nums`}>{count(r.rejected_edits)}</td>
                  <td className={`${TD} tabular-nums`}>{count(r.rework_requests)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
