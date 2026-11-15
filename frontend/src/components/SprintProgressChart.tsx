/**
 * SprintProgressChart — a sprint's two lines over time (issue #531): tasks the
 * producers claimed delivered and tasks the Verifier verified, drawn from the
 * sprint's task events and never merged into one. The gap between them is
 * shown as a number; the table under the chart holds every point.
 *
 * The two series wear the first two categorical slots of the dark reference
 * palette (validated against the dark surface: CVD ΔE 26.8), and differ by
 * line style too (solid against dashed), so identity is never color alone.
 */
import { useTranslation } from 'react-i18next'
import type { ProgressPoint, ProgressSeries } from '../services/api'

const CLAIMED_COLOR = '#3987e5'
const VERIFIED_COLOR = '#d95926'
const WIDTH = 600
const HEIGHT = 200
const PAD = { top: 12, right: 72, bottom: 28, left: 32 }

interface Props {
  series: ProgressSeries
}

function coordinates(points: ProgressPoint[], key: 'claimed' | 'verified', max: number) {
  const plotWidth = WIDTH - PAD.left - PAD.right
  const plotHeight = HEIGHT - PAD.top - PAD.bottom
  return points.map((point, index) => ({
    x: PAD.left + (points.length === 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth),
    y: PAD.top + plotHeight - (max === 0 ? 0 : (point[key] / max) * plotHeight),
    point,
  }))
}

export function SprintProgressChart({ series }: Props) {
  const { t } = useTranslation()
  const points = series.points
  const claimedLabel = t('sprintProgress.claimed', { defaultValue: 'Delivered (claimed)' })
  const verifiedLabel = t('sprintProgress.verified', { defaultValue: 'Verified' })

  if (points.length === 0) {
    return <p className="text-xs text-gray-500">{t('sprintProgress.noEvents', { defaultValue: 'No task events yet.' })}</p>
  }

  const max = Math.max(series.tasks, 1)
  const claimed = coordinates(points, 'claimed', max)
  const verified = coordinates(points, 'verified', max)
  const baseline = HEIGHT - PAD.bottom
  const lines = [
    { key: 'claimed', label: claimedLabel, color: CLAIMED_COLOR, dash: undefined, coords: claimed },
    { key: 'verified', label: verifiedLabel, color: VERIFIED_COLOR, dash: '6 4', coords: verified },
  ]

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-4 text-[11px] text-gray-400" aria-label={t('sprintProgress.legend', { defaultValue: 'Legend' })}>
        {lines.map(line => (
          <span key={line.key} className="flex items-center gap-1.5">
            <svg width="20" height="8" aria-hidden="true">
              <line x1="0" y1="4" x2="20" y2="4" stroke={line.color} strokeWidth="2" strokeDasharray={line.dash} />
            </svg>
            {line.label}
          </span>
        ))}
      </div>
      <svg
        role="img"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-48 w-full"
        aria-label={t('sprintProgress.chartLabel', {
          defaultValue: '{{claimed}} of {{tasks}} tasks claimed delivered, {{verified}} verified',
          claimed: series.final.claimed,
          verified: series.final.verified,
          tasks: series.tasks,
        })}
      >
        <line x1={PAD.left} y1={baseline} x2={WIDTH - PAD.right} y2={baseline} stroke="#334155" strokeWidth="1" />
        <line x1={PAD.left} y1={PAD.top} x2={WIDTH - PAD.right} y2={PAD.top} stroke="#1e293b" strokeWidth="1" />
        <text x={PAD.left - 6} y={baseline} textAnchor="end" dominantBaseline="middle" className="fill-gray-500 text-[10px]">0</text>
        <text x={PAD.left - 6} y={PAD.top} textAnchor="end" dominantBaseline="middle" className="fill-gray-500 text-[10px]">{series.tasks}</text>
        <text x={PAD.left} y={HEIGHT - 8} className="fill-gray-500 text-[10px]">{points[0].at.slice(0, 10)}</text>
        {points.length > 1 && (
          <text x={WIDTH - PAD.right} y={HEIGHT - 8} textAnchor="end" className="fill-gray-500 text-[10px]">
            {points[points.length - 1].at.slice(0, 10)}
          </text>
        )}
        {lines.map(line => (
          <g key={line.key}>
            {line.coords.length > 1 && (
              <polyline
                fill="none"
                stroke={line.color}
                strokeWidth="2"
                strokeLinejoin="round"
                strokeDasharray={line.dash}
                points={line.coords.map(c => `${c.x},${c.y}`).join(' ')}
              />
            )}
            {line.coords.map(c => (
              <g key={`${line.key}-${c.point.at}-${c.point.events}`}>
                <circle cx={c.x} cy={c.y} r="4" fill={line.color} stroke="#0f172a" strokeWidth="2" />
                <circle cx={c.x} cy={c.y} r="10" fill="transparent">
                  <title>{`${c.point.at} · ${line.label}: ${c.point[line.key as 'claimed' | 'verified']}`}</title>
                </circle>
              </g>
            ))}
            <text
              x={line.coords[line.coords.length - 1].x + 8}
              y={line.coords[line.coords.length - 1].y + (line.key === 'claimed' ? -6 : 10)}
              className="fill-gray-300 text-[10px]"
            >
              {`${line.label} ${line.coords[line.coords.length - 1].point[line.key as 'claimed' | 'verified']}`}
            </text>
          </g>
        ))}
      </svg>
      <details className="text-xs text-gray-400">
        <summary className="cursor-pointer">{t('sprintProgress.table', { defaultValue: 'Show as a table' })}</summary>
        <table className="mt-2 w-full text-left">
          <thead>
            <tr className="text-gray-500">
              <th className="py-1 pr-3 font-medium">{t('sprintProgress.at', { defaultValue: 'When' })}</th>
              <th className="py-1 pr-3 text-right font-medium">{claimedLabel}</th>
              <th className="py-1 pr-3 text-right font-medium">{verifiedLabel}</th>
              <th className="py-1 pr-3 text-right font-medium">{t('sprintProgress.gap', { defaultValue: 'Claimed, not verified' })}</th>
              <th className="py-1 text-right font-medium">{t('sprintProgress.done', { defaultValue: 'Done' })}</th>
            </tr>
          </thead>
          <tbody>
            {points.map(point => (
              <tr key={`${point.at}-${point.events}`} className="border-t border-surface-800">
                <td className="py-1 pr-3">{point.at}</td>
                <td className="py-1 pr-3 text-right tabular-nums">{point.claimed}</td>
                <td className="py-1 pr-3 text-right tabular-nums">{point.verified}</td>
                <td className="py-1 pr-3 text-right tabular-nums">{point.gap}</td>
                <td className="py-1 text-right tabular-nums">{point.status.done ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  )
}
