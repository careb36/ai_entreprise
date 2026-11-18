/**
 * Rework requests whose text names a missing module, package or import,
 * before and after the grounded check shipped (issue #549, the second
 * measure), on the Sprints page beside the gate's refutations. Unlike that
 * panel, every count here is a CLAIM: what reviewers (the gate, people, agent
 * reviewers) wrote, not whether a module was in fact missing. So it wears the
 * design system's claim look: a dashed panel under a speech mark, and dashed
 * outlined columns, never the solid marks of an observation.
 *
 * One series (requests naming a missing module), so one hue and no legend;
 * each week's column has its value on hover and focus, the cutoff is a rule
 * between the weeks before and after it, and the weeks are also a table.
 */
import { useTranslation } from 'react-i18next'
import { MessageSquareQuote } from 'lucide-react'
import {
  allWeeks,
  mentionShare,
  sourcesOf,
  totals,
  useMissingModuleMentions,
  type MentionCounts,
  type MentionWeek,
  type MissingModuleMentions as Data,
  type Side,
} from '../services/missingModuleMentions'
import { CountPill, Panel } from './ui'

const SOURCE_KEYS: Record<string, string> = { gate: 'gate', person: 'person', agent_reviewer: 'agentReviewer' }

const COLUMN_WIDTH = 14
const COLUMN_GAP = 2
const CUTOFF_GAP = 9
const CHART_HEIGHT = 40
const DATA_END_RADIUS = 4

function useSourceLabel() {
  const { t } = useTranslation()
  return (source: string) => {
    const key = SOURCE_KEYS[source]
    return key ? t(`missingModuleMentions.source.${key}`) : source
  }
}

function columnPath(x: number, top: number, bottom: number) {
  const r = Math.min(DATA_END_RADIUS, COLUMN_WIDTH / 2, Math.max(0, bottom - top))
  // Rounded at the data end, square at the baseline.
  return `M${x},${bottom} V${top + r} Q${x},${top} ${x + r},${top} H${x + COLUMN_WIDTH - r} Q${x + COLUMN_WIDTH},${top} ${x + COLUMN_WIDTH},${top + r} V${bottom} Z`
}

/** "2026-11-12T12:33:51" → "2026-11-12 12:33 UTC". */
function formatCutoff(iso: string) {
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`.trim()
}

function percent(share: number | null) {
  return share === null ? '—' : `${Math.round(share * 100)}%`
}

function WeeklyColumns({ weeks }: { weeks: MentionWeek[] }) {
  const { t } = useTranslation()
  const values = weeks.map(week => totals(week).mentions)
  const max = Math.max(...values, 0)
  const firstAfter = weeks.findIndex(week => week.period === 'after')
  const hasCutoff = firstAfter > 0
  const x = (index: number) => index * (COLUMN_WIDTH + COLUMN_GAP) + (hasCutoff && index >= firstAfter ? CUTOFF_GAP : 0)
  const width = weeks.length * (COLUMN_WIDTH + COLUMN_GAP) - COLUMN_GAP + (hasCutoff ? CUTOFF_GAP : 0)
  const cutoffX = hasCutoff ? x(firstAfter) - (CUTOFF_GAP + COLUMN_GAP) / 2 : 0
  return (
    <div>
      <svg width={width} height={CHART_HEIGHT} viewBox={`0 0 ${width} ${CHART_HEIGHT}`} role="img"
        aria-label={t('missingModuleMentions.chartLabel')} className="overflow-visible">
        {weeks.map((week, index) => {
          const value = values[index]
          const { requests } = totals(week)
          // A zero week still shows as a hairline so the week is visibly there.
          const height = max > 0 ? Math.max(1, Math.round((value / max) * CHART_HEIGHT)) : 1
          return (
            <path key={`${week.period}-${week.week}`}
              d={columnPath(x(index), CHART_HEIGHT - height, CHART_HEIGHT)}
              className={`${value > 0 ? 'fill-brand-500/25 stroke-brand-400' : 'fill-surface-700'} outline-none hover:opacity-80 focus:opacity-80`}
              strokeDasharray={value > 0 ? '3 2' : undefined} strokeWidth={value > 0 ? 1 : undefined}
              tabIndex={0}>
              <title>{t('missingModuleMentions.weekValue', {
                week: week.week, side: t(`missingModuleMentions.${week.period}`), count: value, requests,
              })}</title>
            </path>
          )
        })}
        {hasCutoff && (
          <line x1={cutoffX} x2={cutoffX} y1={-4} y2={CHART_HEIGHT} className="stroke-gray-400" strokeWidth={1}>
            <title>{t('missingModuleMentions.cutoffMark')}</title>
          </line>
        )}
      </svg>
      <div className="text-meta mt-1 flex justify-between tabular-nums" style={{ width }}>
        <span>{weeks[0]?.week}</span>
        {weeks.length > 1 && <span>{weeks[weeks.length - 1].week}</span>}
      </div>
    </div>
  )
}

function SideFigure({ side, counts, days }: { side: Side; counts: MentionCounts; days: number }) {
  const { t } = useTranslation()
  const { requests, mentions } = totals(counts)
  return (
    <div className="rounded-md border border-dashed border-surface-600 px-3 py-2">
      <dt className="text-meta">{t(`missingModuleMentions.${side}`)}</dt>
      <dd className="text-lg font-semibold tabular-nums text-gray-100">{percent(mentionShare(counts))}</dd>
      <dd className="text-xs tabular-nums text-gray-400">
        {t('missingModuleMentions.ofRequests', { count: requests, mentions })}
      </dd>
      <dd className="text-meta tabular-nums">{t('missingModuleMentions.days', { count: days })}</dd>
    </div>
  )
}

function BySource({ data }: { data: Data }) {
  const { t } = useTranslation()
  const label = useSourceLabel()
  const cell = (counts: MentionCounts, source: string) =>
    `${counts.requests_whose_text_names_missing_module[source] ?? 0} / ${counts.rework_requests[source] ?? 0}`
  return (
    <table className="w-full text-left text-xs text-gray-300">
      <caption className="mb-1 text-left text-xs text-gray-400">{t('missingModuleMentions.bySource')}</caption>
      <thead className="text-gray-400">
        <tr>
          <th scope="col" className="py-1 pr-3 font-medium">{t('missingModuleMentions.sourceColumn')}</th>
          <th scope="col" className="py-1 pr-3 text-right font-medium">{t('missingModuleMentions.before')}</th>
          <th scope="col" className="py-1 text-right font-medium">{t('missingModuleMentions.after')}</th>
        </tr>
      </thead>
      <tbody>
        {sourcesOf(data).map(source => (
          <tr key={source} className="border-t border-surface-700">
            <th scope="row" className="py-1 pr-3 font-normal">{label(source)}</th>
            <td className="py-1 pr-3 text-right tabular-nums">{cell(data.before, source)}</td>
            <td className="py-1 text-right tabular-nums">{cell(data.after, source)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function WeeksTable({ weeks }: { weeks: MentionWeek[] }) {
  const { t } = useTranslation()
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-gray-400 hover:text-brand-300">{t('missingModuleMentions.showTable')}</summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-xs text-gray-300">
          <caption className="sr-only">{t('missingModuleMentions.tableCaption')}</caption>
          <thead className="text-gray-400">
            <tr>
              <th scope="col" className="py-1 pr-3 font-medium">{t('missingModuleMentions.week')}</th>
              <th scope="col" className="py-1 pr-3 font-medium">{t('missingModuleMentions.sideColumn')}</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">{t('missingModuleMentions.mentionsColumn')}</th>
              <th scope="col" className="py-1 text-right font-medium">{t('missingModuleMentions.requestsColumn')}</th>
            </tr>
          </thead>
          <tbody>
            {weeks.map(week => {
              const { requests, mentions } = totals(week)
              return (
                <tr key={`${week.period}-${week.week}`} className="border-t border-surface-700">
                  <th scope="row" className="py-1 pr-3 font-normal tabular-nums">{week.week}</th>
                  <td className="py-1 pr-3">{t(`missingModuleMentions.${week.period}`)}</td>
                  <td className="py-1 pr-3 text-right tabular-nums">{mentions}</td>
                  <td className="py-1 text-right tabular-nums">{requests}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </details>
  )
}

export function MissingModuleMentions({ companyId, sprintId }: { companyId: number; sprintId?: number }) {
  const { t } = useTranslation()
  const { data } = useMissingModuleMentions(companyId, { sprintId })
  if (!data) return null
  const before = totals(data.before)
  const after = totals(data.after)
  const mentions = before.mentions + after.mentions
  const weeks = allWeeks(data)
  const cutoff = formatCutoff(data.cutoff)

  return (
    <Panel title={t('missingModuleMentions.title')} className="border-dashed"
      count={<CountPill count={mentions} label={t('missingModuleMentions.count', { count: mentions })} />}>
      <div className="space-y-3 p-3 text-sm">
        <p className="flex items-start gap-1.5 text-xs text-gray-400">
          <MessageSquareQuote size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span title={data.counts_what}>{t('missingModuleMentions.claimed')}</span>
        </p>
        <p className="text-meta tabular-nums">
          {data.cutoff_is_default
            ? t('missingModuleMentions.cutoffDefault', { cutoff })
            : t('missingModuleMentions.cutoffChosen', { cutoff })}
        </p>
        {before.requests + after.requests === 0 ? (
          <p className="text-xs text-gray-400">{t('missingModuleMentions.none')}</p>
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-2 sm:max-w-sm" aria-label={t('missingModuleMentions.beforeAfter')}>
              <SideFigure side="before" counts={data.before} days={data.before.days} />
              <SideFigure side="after" counts={data.after} days={data.after.days} />
            </dl>
            <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto]">
              <div>
                <BySource data={data} />
                <p className="text-meta mt-1">{t('missingModuleMentions.gateHint')}</p>
              </div>
              <div>
                <p className="mb-1 text-xs text-gray-400">{t('missingModuleMentions.byWeek')}</p>
                <WeeklyColumns weeks={weeks} />
              </div>
            </div>
            <WeeksTable weeks={weeks} />
          </>
        )}
      </div>
    </Panel>
  )
}
