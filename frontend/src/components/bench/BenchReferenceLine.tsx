import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { FlaskConical } from 'lucide-react'
import { formatStamp } from '../../lib/benchFormat'
import type { BenchReference } from '../../lib/benchReference'

/**
 * Under a Sprints page tile (issue #686): the bench's value for the same
 * number, named as the bench's reference, with the scorecard's date and a link
 * to /bench. Drawn only when the latest valid card measured it; the caller
 * passes `null` otherwise and nothing is shown.
 */
export function BenchReferenceLine({ reference, format }: {
  reference: BenchReference | null
  format: (value: number) => string
}) {
  const { t, i18n } = useTranslation()
  if (!reference) return null
  return (
    <p className="mt-1 flex flex-wrap items-center gap-x-1 text-[11px] text-gray-400" data-testid="bench-reference">
      <FlaskConical size={12} aria-hidden="true" className="shrink-0" />
      <span>{t('bench.reference.value', { value: format(reference.value) })}</span>
      <span className="text-gray-500">{t('bench.reference.date', { date: formatStamp(reference.stamp, i18n.language) })}</span>
      <Link to="/bench" className="rounded-sm text-brand-300 hover:text-brand-200">{t('bench.reference.link')}</Link>
    </p>
  )
}

/**
 * The same value as a dashed rule across a tile's sparkline, on the
 * sparkline's own scale. Decorative: the line above says it in words.
 */
export function SparkReferenceRule({ value, max, width, height }: {
  value: number
  max: number
  width: number
  height: number
}) {
  const y = max > 0 ? height - (value / max) * height : height
  return (
    <line x1={0} x2={width} y1={y} y2={y} stroke="currentColor" strokeWidth={1} strokeDasharray="3 2"
      className="text-gray-300" aria-hidden="true" data-testid="bench-reference-rule" />
  )
}
