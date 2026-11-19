import { useTranslation } from 'react-i18next'
import { CheckCircle2, XCircle } from 'lucide-react'
import { Panel, StatusBadge, TABLE, TD, TH, TR } from '../ui'
import { MeasureValue } from './MeasureValue'
import { Fact, SpecStatusBadge } from './BenchLatestCard'
import { formatCount, formatShare } from '../../lib/benchFormat'
import type { BenchSweBench, BenchSweBenchInstance } from '../../services/api'

/** An instance's outcome: resolved or failed as its own tests said, or "Not measured" with the card's own reason. */
function Outcome({ row }: { row: BenchSweBenchInstance }) {
  const { t } = useTranslation()
  const { outcome } = row
  if (outcome.measured) {
    const pass = outcome.value === 'pass'
    return (
      <>
        <StatusBadge
          icon={pass ? CheckCircle2 : XCircle}
          tone={pass ? 'success' : 'danger'}
          label={t(`bench.swebench.outcomes.${outcome.value}`)}
        />
        {!pass && row.reason && <span className="ml-1 text-meta">{row.reason}</span>}
      </>
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
 * The latest card's SWE-bench Lite section (schema v6): the resolve rate over
 * the instances measured by their own tests, the counts, the dataset as the
 * bench pinned it and each instance's outcome. An unmeasured instance, its
 * tests that did not run and a rate with nothing measured say so, never a 0.
 */
export function SweBenchPanel({ data }: { data: BenchSweBench }) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language
  const count = (v: number) => formatCount(v, locale)
  const tests = (v: { passed: number; total: number }) =>
    t('bench.swebench.tests', { passed: count(v.passed), total: count(v.total) })
  return (
    <Panel title={t('bench.swebench.title')}>
      <div className="space-y-3 p-3">
        <p className="text-xs text-gray-400">{t('bench.swebench.note')}</p>
        <dl role="group" aria-label={t('bench.swebench.title')} className="grid gap-3 text-xs sm:grid-cols-3 lg:grid-cols-7">
          <Fact label={t('bench.swebench.resolvedRate')}>
            <MeasureValue measure={data.resolved_rate} format={v => formatShare(v, locale)} />
          </Fact>
          <Fact label={t('bench.swebench.graded')}><span className="tabular-nums">{count(data.graded)}</span></Fact>
          <Fact label={t('bench.swebench.measured')}><span className="tabular-nums">{count(data.measured)}</span></Fact>
          <Fact label={t('bench.swebench.resolved')}><span className="tabular-nums">{count(data.resolved)}</span></Fact>
          <Fact label={t('bench.swebench.failed')}><span className="tabular-nums">{count(data.failed)}</span></Fact>
          <Fact label={t('bench.swebench.unmeasured')}><span className="tabular-nums">{count(data.unmeasured)}</span></Fact>
          <Fact label={t('bench.swebench.tokens')}>
            <MeasureValue measure={data.tokens} format={count} />
            {data.tokens.turns_unreported > 0 && (
              <span className="ml-1 text-meta">{t('bench.unreportedTurns', { count: data.tokens.turns_unreported })}</span>
            )}
          </Fact>
        </dl>
        <p className="break-all text-xs text-gray-400">
          {t('bench.swebench.dataset', { ...data.dataset, pinned: count(data.dataset.pinned) })}
        </p>
        {data.skipped.length > 0 && (
          <p className="text-xs text-gray-300">{t('bench.swebench.skipped', { instances: data.skipped.join(', ') })}</p>
        )}
      </div>
      {data.instances.length > 0 && (
        <div className="overflow-x-auto">
          <table className={TABLE}>
            <caption className="sr-only">{t('bench.swebench.caption')}</caption>
            <thead>
              <tr>
                <th scope="col" className={TH}>{t('bench.swebench.instance')}</th>
                <th scope="col" className={TH}>{t('bench.swebench.outcome')}</th>
                <th scope="col" className={TH}>{t('bench.swebench.failToPass')}</th>
                <th scope="col" className={TH}>{t('bench.swebench.passToPass')}</th>
                <th scope="col" className={TH}>{t('bench.cols.status')}</th>
                <th scope="col" className={TH}>{t('bench.cols.tokens')}</th>
                <th scope="col" className={TH}>{t('bench.cols.wallClock')}</th>
              </tr>
            </thead>
            <tbody>
              {data.instances.map(r => (
                <tr key={r.instance_id} className={TR}>
                  <th scope="row" className={`${TD} font-medium`}>{r.instance_id}</th>
                  <td className={TD}><Outcome row={r} /></td>
                  <td className={TD}><MeasureValue measure={r.fail_to_pass} format={tests} /></td>
                  <td className={TD}><MeasureValue measure={r.pass_to_pass} format={tests} /></td>
                  <td className={TD}><SpecStatusBadge status={r.status} /></td>
                  <td className={TD}><MeasureValue measure={r.tokens} format={count} /></td>
                  <td className={`${TD} tabular-nums`}>
                    {r.wall_clock_seconds === null ? '–' : t('bench.seconds', { value: count(r.wall_clock_seconds) })}
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
