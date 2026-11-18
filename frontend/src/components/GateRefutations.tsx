/**
 * Deliveries the gate before QA refuted, per problem and per week (issue #549),
 * on the Sprints page beside the other learning tiles. Every count is an
 * observation: the Verifier's own send-backs, which the backend wrote to the
 * task-event chain; no agent's message is read. A send-back whose write the
 * daemon never confirmed is said apart, never counted as a refutation.
 *
 * One series (refutations), so one hue and no legend; each week's column has
 * its value on hover and focus, and the weeks are also a table.
 */
import { useTranslation } from 'react-i18next'
import { ShieldCheck } from 'lucide-react'
import {
  GATE_UNCONFIRMED,
  allWeeks,
  refutingProblems,
  useGateRefutations,
  weekRefuted,
  type GateRefutationWeek,
} from '../services/gateRefutations'
import { CountPill, Panel } from './ui'

/** The backend's problem names (`services/evidence/delivery_gate.py`) → their i18n keys. */
const PROBLEM_KEYS: Record<string, string> = {
  'delivery refuted': 'deliveryRefuted',
  'tests failed': 'testsFailed',
  'acceptance tests failed': 'acceptanceTestsFailed',
  'acceptance test edited': 'acceptanceTestEdited',
  'standards violated': 'standardsViolated',
  'security scan failed': 'securityScanFailed',
  'invented dependency or import': 'inventedDependency',
  'dependency install failed': 'dependencyInstallFailed',
  'application failed to start': 'applicationFailedToStart',
  'end-to-end tests failed': 'endToEndTestsFailed',
  'security probe refuted': 'securityProbeRefuted',
  'required command failed': 'requiredCommandFailed',
  [GATE_UNCONFIRMED]: 'deliveryUnconfirmed',
}

const COLUMN_WIDTH = 14
const COLUMN_GAP = 2
const CHART_HEIGHT = 40
const DATA_END_RADIUS = 4

function useProblemLabel() {
  const { t } = useTranslation()
  return (problem: string) => {
    const key = PROBLEM_KEYS[problem]
    return key ? t(`gateRefutations.problem.${key}`, { defaultValue: problem }) : problem
  }
}

function columnPath(x: number, top: number, bottom: number) {
  const r = Math.min(DATA_END_RADIUS, COLUMN_WIDTH / 2, Math.max(0, bottom - top))
  // Rounded at the data end, square at the baseline.
  return `M${x},${bottom} V${top + r} Q${x},${top} ${x + r},${top} H${x + COLUMN_WIDTH - r} Q${x + COLUMN_WIDTH},${top} ${x + COLUMN_WIDTH},${top + r} V${bottom} Z`
}

function WeeklyColumns({ weeks }: { weeks: GateRefutationWeek[] }) {
  const { t } = useTranslation()
  const values = weeks.map(weekRefuted)
  const max = Math.max(...values, 0)
  const width = weeks.length * (COLUMN_WIDTH + COLUMN_GAP) - COLUMN_GAP
  return (
    <div>
      <svg width={width} height={CHART_HEIGHT} viewBox={`0 0 ${width} ${CHART_HEIGHT}`} role="img"
        aria-label={t('gateRefutations.chartLabel')} className="overflow-visible">
        {weeks.map((week, index) => {
          const value = values[index]
          // A zero week still shows as a hairline so the week is visibly there.
          const height = max > 0 ? Math.max(1, Math.round((value / max) * CHART_HEIGHT)) : 1
          return (
            <path key={week.week} d={columnPath(index * (COLUMN_WIDTH + COLUMN_GAP), CHART_HEIGHT - height, CHART_HEIGHT)}
              className={`${value > 0 ? 'fill-brand-500' : 'fill-surface-700'} outline-none hover:opacity-80 focus:opacity-80`}
              tabIndex={0}>
              <title>{t('gateRefutations.weekValue', { week: week.week, count: value })}</title>
            </path>
          )
        })}
      </svg>
      <div className="text-meta mt-1 flex justify-between tabular-nums" style={{ width }}>
        <span>{weeks[0]?.week}</span>
        {weeks.length > 1 && <span>{weeks[weeks.length - 1].week}</span>}
      </div>
    </div>
  )
}

function WeeksTable({ weeks, problems }: { weeks: GateRefutationWeek[]; problems: string[] }) {
  const { t } = useTranslation()
  const label = useProblemLabel()
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-gray-400 hover:text-brand-300">{t('gateRefutations.showTable')}</summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-xs text-gray-300">
          <caption className="sr-only">{t('gateRefutations.tableCaption')}</caption>
          <thead className="text-gray-400">
            <tr>
              <th scope="col" className="py-1 pr-3 font-medium">{t('gateRefutations.week')}</th>
              {problems.map(problem => (
                <th key={problem} scope="col" className="py-1 pr-3 text-right font-medium">{label(problem)}</th>
              ))}
              <th scope="col" className="py-1 text-right font-medium">{t('gateRefutations.refutedColumn')}</th>
            </tr>
          </thead>
          <tbody>
            {weeks.map(week => (
              <tr key={week.week} className="border-t border-surface-700">
                <th scope="row" className="py-1 pr-3 font-normal tabular-nums">{week.week}</th>
                {problems.map(problem => (
                  <td key={problem} className="py-1 pr-3 text-right tabular-nums">{week.by_problem[problem] ?? 0}</td>
                ))}
                <td className="py-1 text-right tabular-nums">{weekRefuted(week)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

export function GateRefutations({ companyId, sprintId }: { companyId: number; sprintId?: number }) {
  const { t } = useTranslation()
  const label = useProblemLabel()
  const { data } = useGateRefutations(companyId, { sprintId })
  if (!data) return null
  const problems = refutingProblems(data)
  const max = problems.reduce((top, [, count]) => Math.max(top, count), 0)
  const weeks = allWeeks(data)
  const tableProblems = problems.map(([problem]) => problem)
  if (data.unconfirmed > 0) tableProblems.push(GATE_UNCONFIRMED)

  return (
    <Panel title={t('gateRefutations.title')}
      count={<CountPill count={data.refuted} label={t('gateRefutations.count', { count: data.refuted })} />}>
      <div className="space-y-3 p-3 text-sm">
        <p className="flex items-center gap-1.5 text-xs text-gray-400">
          <ShieldCheck size={13} className="shrink-0" aria-hidden="true" />
          <span>{t('gateRefutations.observed')}</span>
        </p>
        <p className="text-meta tabular-nums">{t('gateRefutations.period', { since: data.since, until: data.until })}</p>
        {problems.length === 0 ? (
          <p className="text-xs text-gray-400">{t('gateRefutations.none')}</p>
        ) : (
          <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto]">
            <ul className="space-y-1" aria-label={t('gateRefutations.byProblem')}>
              {problems.map(([problem, count]) => (
                <li key={problem} className="grid grid-cols-[minmax(0,12rem)_minmax(0,1fr)_2.5rem] items-center gap-2 text-xs">
                  <span className="truncate text-gray-300" title={label(problem)}>{label(problem)}</span>
                  <span className="h-1.5 overflow-hidden rounded-full bg-surface-700" aria-hidden="true">
                    <span className="block h-full rounded-full bg-brand-500" style={{ width: `${Math.round((count / max) * 100)}%` }} />
                  </span>
                  <span className="text-right tabular-nums text-gray-300">{count}</span>
                </li>
              ))}
            </ul>
            <div>
              <p className="mb-1 text-xs text-gray-400">{t('gateRefutations.byWeek')}</p>
              <WeeklyColumns weeks={weeks} />
            </div>
          </div>
        )}
        {data.unconfirmed > 0 && (
          <p className="text-xs text-gray-400">{t('gateRefutations.unconfirmed', { count: data.unconfirmed })}</p>
        )}
        {(problems.length > 0 || data.unconfirmed > 0) && <WeeksTable weeks={weeks} problems={tableProblems} />}
      </div>
    </Panel>
  )
}
