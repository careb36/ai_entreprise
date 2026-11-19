import { useTranslation } from 'react-i18next'
import { CircleSlash } from 'lucide-react'
import type { BenchMeasure, BenchUnmeasuredReason } from '../../services/api'

/** Why a metric was not measured, in words. */
export function useUnmeasuredReason() {
  const { t } = useTranslation()
  return (reason: BenchUnmeasuredReason) => t(`bench.reasons.${reason}`)
}

/**
 * A metric the scorer measured, formatted, or "Not measured" with the card's
 * reason: never a 0 in its place. The reason is the badge's title and is read
 * out beside it.
 */
export function MeasureValue<T>({ measure, format }: {
  measure: BenchMeasure<T>
  format: (value: T) => string
}) {
  const { t } = useTranslation()
  const reasonText = useUnmeasuredReason()
  if (measure.measured) return <span className="tabular-nums">{format(measure.value)}</span>
  const reason = reasonText(measure.reason)
  return (
    <span className="status-badge border-surface-700 bg-surface-800 text-gray-300" title={reason} data-measured="false">
      <CircleSlash size={11} aria-hidden="true" className="flex-shrink-0" />
      {t('bench.notMeasured')}
      <span className="sr-only">{t('bench.notMeasuredBecause', { reason })}</span>
    </span>
  )
}
