import { useTranslation } from 'react-i18next'
import { CheckCircle2, XCircle } from 'lucide-react'
import { Panel, StatusBadge, TABLE, TD, TH, TR } from '../ui'
import { MeasureValue } from './MeasureValue'
import { Fact, SpecStatusBadge } from './BenchLatestCard'
import { formatCount, formatShare } from '../../lib/benchFormat'
import type { BenchHumanEval, BenchHumanEvalProblem } from '../../services/api'

/** A problem's outcome: pass or fail as HumanEval's tests said, or "Not measured" with the card's own reason. */
function Outcome({ outcome }: { outcome: BenchHumanEvalProblem['outcome'] }) {
  const { t } = useTranslation()
  if (outcome.measured) {
    const pass = outcome.value === 'pass'
    return (
      <StatusBadge
        icon={pass ? CheckCircle2 : XCircle}
        tone={pass ? 'success' : 'danger'}
        label={t(`bench.humaneval.outcomes.${outcome.value}`)}
      />
    )
  }
  return (
    <>
      <MeasureValue measure={outcome} format={String} />
      {typeof outcome.detail === 'string' && <span className="ml-1 text-meta">{outcome.detail}</span>}
    </>
  )
}

/**
 * The latest card's HumanEval section (schema v5): pass@1 over the problems
 * graded by HumanEval's own tests, the counts, the dataset as the bench
 * pinned it and each problem's outcome. pass@1 is "Not measured" while any
 * problem is, never a 0.
 */
export function HumanEvalPanel({ data }: { data: BenchHumanEval }) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language
  const count = (v: number) => formatCount(v, locale)
  return (
    <Panel title={t('bench.humaneval.title')}>
      <div className="space-y-3 p-3">
        <p className="text-xs text-gray-400">{t('bench.humaneval.note')}</p>
        <dl role="group" aria-label={t('bench.humaneval.title')} className="grid gap-3 text-xs sm:grid-cols-3 lg:grid-cols-6">
          <Fact label={t('bench.humaneval.passAt1')}>
            <MeasureValue measure={data.pass_at_1} format={v => formatShare(v, locale)} />
          </Fact>
          <Fact label={t('bench.humaneval.graded')}><span className="tabular-nums">{count(data.graded)}</span></Fact>
          <Fact label={t('bench.humaneval.passed')}><span className="tabular-nums">{count(data.passed)}</span></Fact>
          <Fact label={t('bench.humaneval.failed')}><span className="tabular-nums">{count(data.failed)}</span></Fact>
          <Fact label={t('bench.humaneval.unmeasured')}><span className="tabular-nums">{count(data.unmeasured)}</span></Fact>
          <Fact label={t('bench.humaneval.tokens')}>
            <MeasureValue measure={data.tokens} format={count} />
            {data.tokens.turns_unreported > 0 && (
              <span className="ml-1 text-meta">{t('bench.unreportedTurns', { count: data.tokens.turns_unreported })}</span>
            )}
          </Fact>
        </dl>
        <p className="break-all text-xs text-gray-400">
          {t('bench.humaneval.dataset', { source: data.dataset.source, sha: data.dataset.sha256, license: data.dataset.license })}
        </p>
        {data.skipped.length > 0 && (
          <p className="text-xs text-gray-300">{t('bench.humaneval.skipped', { problems: data.skipped.join(', ') })}</p>
        )}
      </div>
      {data.problems.length > 0 && (
        <div className="overflow-x-auto">
          <table className={TABLE}>
            <caption className="sr-only">{t('bench.humaneval.caption')}</caption>
            <thead>
              <tr>
                <th scope="col" className={TH}>{t('bench.humaneval.problem')}</th>
                <th scope="col" className={TH}>{t('bench.humaneval.entryPoint')}</th>
                <th scope="col" className={TH}>{t('bench.humaneval.outcome')}</th>
                <th scope="col" className={TH}>{t('bench.cols.status')}</th>
                <th scope="col" className={TH}>{t('bench.cols.tokens')}</th>
                <th scope="col" className={TH}>{t('bench.cols.wallClock')}</th>
              </tr>
            </thead>
            <tbody>
              {data.problems.map(p => (
                <tr key={p.task_id} className={TR}>
                  <th scope="row" className={`${TD} font-medium`}>{p.task_id}</th>
                  <td className={TD}><code>{p.entry_point}</code></td>
                  <td className={TD}><Outcome outcome={p.outcome} /></td>
                  <td className={TD}><SpecStatusBadge status={p.status} /></td>
                  <td className={TD}><MeasureValue measure={p.tokens} format={count} /></td>
                  <td className={`${TD} tabular-nums`}>
                    {p.wall_clock_seconds === null ? '–' : t('bench.seconds', { value: count(p.wall_clock_seconds) })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
