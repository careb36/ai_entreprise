/**
 * Two stat tiles on the Sprints page: rework per task and the share of
 * blocked tasks in the latest closed sprint, the change against the sprint
 * before it, and a sparkline of the last closed sprints. Lower is better for
 * both, so a drop reads as good. Every value is also in a table for readers
 * without a pointer or without colour.
 */
import { useTranslation } from 'react-i18next'
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react'
import type { SprintSummary } from '../services/api'
import { formatRework, formatShare, learningTrend, type SprintPoint } from '../lib/learningTrend'

const SPARK_POINTS = 12
const BAR_WIDTH = 8
const BAR_GAP = 2
const SPARK_HEIGHT = 32
const DATA_END_RADIUS = 3
// Past sprints in the de-emphasis step of the brand ramp, the latest in the accent.
const PAST_FILL = '#3730a3'
const LATEST_FILL = '#818cf8'

function columnPath(x: number, top: number, bottom: number, width: number) {
  const r = Math.min(DATA_END_RADIUS, width / 2, Math.max(0, bottom - top))
  // Rounded at the data end, square at the baseline.
  return `M${x},${bottom} V${top + r} Q${x},${top} ${x + r},${top} H${x + width - r} Q${x + width},${top} ${x + width},${top + r} V${bottom} Z`
}

function Sparkline({ points, pick, format, label }: {
  points: SprintPoint[]
  pick: (point: SprintPoint) => number
  format: (value: number) => string
  label: string
}) {
  const shown = points.slice(-SPARK_POINTS)
  const max = Math.max(...shown.map(pick), 0)
  const width = shown.length * (BAR_WIDTH + BAR_GAP) - BAR_GAP
  return (
    <svg width={width} height={SPARK_HEIGHT} viewBox={`0 0 ${width} ${SPARK_HEIGHT}`} role="img" aria-label={label} className="shrink-0 overflow-visible">
      {shown.map((point, index) => {
        const value = pick(point)
        // A zero still shows as a hairline so the sprint is visibly there.
        const height = max > 0 ? Math.max(1, Math.round((value / max) * SPARK_HEIGHT)) : 1
        const x = index * (BAR_WIDTH + BAR_GAP)
        const latest = index === shown.length - 1
        return (
          <path
            key={point.id}
            d={columnPath(x, SPARK_HEIGHT - height, SPARK_HEIGHT, BAR_WIDTH)}
            fill={latest ? LATEST_FILL : PAST_FILL}
            className="hover:opacity-80 focus:opacity-80"
            tabIndex={0}
          >
            <title>{`${point.name}: ${format(value)}`}</title>
          </path>
        )
      })}
    </svg>
  )
}

function Delta({ change, format }: { change: number; format: (value: number) => string }) {
  const { t } = useTranslation()
  const sign = change > 0 ? '+' : change < 0 ? '−' : ''
  const Icon = change > 0 ? ArrowUpRight : change < 0 ? ArrowDownRight : Minus
  // Lower is better: a drop is good news, a rise is not. The arrow carries the
  // direction too, so the colour is never the only signal.
  const tone = change > 0 ? 'text-red-300' : change < 0 ? 'text-emerald-300' : 'text-gray-500'
  return (
    <p className={`flex items-center gap-1 text-[11px] ${tone}`}>
      <Icon size={12} aria-hidden="true" />
      <span>{sign}{format(Math.abs(change))}</span>
      <span className="text-gray-500">{t('sprints.vsPrevious', { defaultValue: 'vs previous sprint' })}</span>
    </p>
  )
}

function Tile({ label, points, pick, format }: {
  label: string
  points: SprintPoint[]
  pick: (point: SprintPoint) => number
  format: (value: number) => string
}) {
  const latest = points[points.length - 1]
  const previous = points.length >= 2 ? points[points.length - 2] : null
  return (
    <div className="card flex items-end justify-between gap-4 p-4">
      <div className="min-w-0">
        <p className="text-xs text-gray-400">{label}</p>
        <p className="mt-1 text-2xl font-semibold text-white">{format(pick(latest))}</p>
        {previous && <Delta change={pick(latest) - pick(previous)} format={format} />}
      </div>
      {points.length >= 2 && <Sparkline points={points} pick={pick} format={format} label={label} />}
    </div>
  )
}

export function LearningTrend({ sprints }: { sprints: SprintSummary[] }) {
  const { t } = useTranslation()
  const points = learningTrend(sprints)
  if (points.length === 0) return null
  const title = t('sprints.learningTitle', { defaultValue: 'Is the company learning?' })
  const reworkLabel = t('sprints.reworkPerTask', { defaultValue: 'Rework requests per task' })
  const blockedLabel = t('sprints.blockedShare', { defaultValue: 'Share of blocked tasks' })
  return (
    <section aria-label={title} className="space-y-2">
      <div>
        <h2 className="text-sm font-semibold text-white">{title}</h2>
        <p className="text-xs text-gray-500">
          {t('sprints.learningHint', { defaultValue: 'Per closed sprint, oldest to newest. These are the numbers that keep or revert a procedure; lower is better.' })}
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Tile label={reworkLabel} points={points} pick={p => p.reworkPerTask} format={formatRework} />
        <Tile label={blockedLabel} points={points} pick={p => p.blockedShare} format={formatShare} />
      </div>
      <table className="sr-only">
        <caption>{t('sprints.trendTable', { defaultValue: 'Closed sprints, oldest first' })}</caption>
        <thead>
          <tr>
            <th scope="col">{t('sprints.sprintColumn', { defaultValue: 'Sprint' })}</th>
            <th scope="col">{reworkLabel}</th>
            <th scope="col">{blockedLabel}</th>
          </tr>
        </thead>
        <tbody>
          {points.map(point => (
            <tr key={point.id}>
              <th scope="row">{point.name}</th>
              <td>{formatRework(point.reworkPerTask)}</td>
              <td>{formatShare(point.blockedShare)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
