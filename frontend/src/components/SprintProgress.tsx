/**
 * SprintProgress — the Sprint page's progress card (issue #531): the claimed
 * and verified lines from `GET /v1/sprints/{id}/progress`, the tasks by their
 * latest verdict, whether the closed sprint's snapshot still matches the
 * digest sealed in the audit chain, and, for a sprint planned from a
 * specification, each requirement's coverage across its sprints.
 */
import { useTranslation } from 'react-i18next'
import { useSpecificationProgress, useSprintProgress } from '../hooks/useSprintProgress'
import type { CoverageState, ProgressVerdict, SprintProgress as Progress } from '../services/api'
import { SprintProgressChart } from './SprintProgressChart'

const VERDICTS: ProgressVerdict[] = ['verified', 'refuted', 'unverifiable', 'none']
const REFRESH_MS = 30_000

function Seal({ progress }: { progress: Progress }) {
  const { t } = useTranslation()
  if (progress.source === 'live') {
    return <span className="text-[11px] text-gray-500">{t('sprintProgress.live', { defaultValue: 'Live, from the task events' })}</span>
  }
  const snapshot = progress.snapshot
  const intact = snapshot?.verification?.intact
  return (
    <span className={`text-[11px] ${intact ? 'text-gray-400' : 'text-red-300'}`} title={snapshot?.digest}>
      {intact
        ? t('sprintProgress.sealed', { defaultValue: 'Sealed at close {{n}}; digest and audit chain verified', n: snapshot?.close_number })
        : t('sprintProgress.sealBroken', { defaultValue: 'Sealed at close {{n}}; the digest or the audit chain does not verify', n: snapshot?.close_number })}
    </span>
  )
}

function Coverage({ specId }: { specId: number }) {
  const { t } = useTranslation()
  const query = useSpecificationProgress(specId)
  if (!query.data || query.data.requirements.length === 0) return null
  const label = (state: CoverageState) => t(`sprintProgress.coverage.${state}`, { defaultValue: state })
  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold text-gray-300">
        {t('sprintProgress.coverageTitle', { defaultValue: 'Requirements across the specification’s sprints' })}
      </h4>
      <table className="w-full text-left text-xs text-gray-400">
        <thead>
          <tr className="text-gray-500">
            <th className="py-1 pr-3 font-medium">{t('sprintProgress.requirement', { defaultValue: 'Requirement' })}</th>
            <th className="py-1 pr-3 text-right font-medium">{t('sprintProgress.tasks', { defaultValue: 'Tasks' })}</th>
            <th className="py-1 pr-3 text-right font-medium">{t('sprintProgress.claimed', { defaultValue: 'Delivered (claimed)' })}</th>
            <th className="py-1 pr-3 text-right font-medium">{t('sprintProgress.done', { defaultValue: 'Done' })}</th>
            <th className="py-1 pr-3 text-right font-medium">{t('sprintProgress.verified', { defaultValue: 'Verified' })}</th>
            <th className="py-1 font-medium">{t('sprintProgress.state', { defaultValue: 'State' })}</th>
          </tr>
        </thead>
        <tbody>
          {query.data.requirements.map(row => (
            <tr key={row.requirement} className="border-t border-surface-800">
              <td className="py-1 pr-3 font-mono">{row.requirement}</td>
              <td className="py-1 pr-3 text-right tabular-nums">{row.tasks.length}</td>
              <td className="py-1 pr-3 text-right tabular-nums">{row.claimed}</td>
              <td className="py-1 pr-3 text-right tabular-nums">{row.done}</td>
              <td className="py-1 pr-3 text-right tabular-nums">{row.verified}</td>
              <td className="py-1">{label(row.state)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function SprintProgress({ sprintId, live = false, specificationId }: {
  sprintId: number
  live?: boolean
  specificationId?: number | null
}) {
  const { t } = useTranslation()
  const query = useSprintProgress(sprintId, live ? REFRESH_MS : false)
  const progress = query.data
  return (
    <section className="card space-y-4 p-4" aria-labelledby="sprint-progress-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="sprint-progress-title" className="text-sm font-semibold text-gray-200">
          {t('sprintProgress.title', { defaultValue: 'Claimed done versus verified' })}
        </h3>
        {progress && <Seal progress={progress} />}
      </div>
      {query.isError ? (
        <p role="alert" className="text-xs text-red-300">{t('sprintProgress.loadError', { defaultValue: 'Could not load the sprint’s progress.' })}</p>
      ) : !progress ? (
        <p role="status" className="text-xs text-gray-500">{t('common.loading', { defaultValue: 'Loading...' })}</p>
      ) : (
        <>
          <p className="text-xs text-gray-400">
            {t('sprintProgress.gapSummary', {
              defaultValue: '{{gap}} of {{claimed}} tasks claimed delivered are not verified.',
              gap: progress.series.final.gap,
              claimed: progress.series.final.claimed,
            })}
          </p>
          <SprintProgressChart series={progress.series} />
          <dl className="flex flex-wrap gap-4 text-[11px] text-gray-400">
            {VERDICTS.map(verdict => (
              <div key={verdict} className="flex gap-1">
                <dt>{t(`sprintProgress.verdict.${verdict}`, { defaultValue: verdict })}</dt>
                <dd className="tabular-nums text-gray-200">{progress.series.final.verification[verdict] ?? 0}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
      {typeof specificationId === 'number' && <Coverage specId={specificationId} />}
    </section>
  )
}
