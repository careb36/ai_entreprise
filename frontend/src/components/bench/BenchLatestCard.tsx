import { useTranslation } from 'react-i18next'
import { AlertTriangle, CheckCircle2, Clock, Scissors, XCircle } from 'lucide-react'
import { Panel, StatusBadge, type StatusTone, TABLE, TD, TH, TR } from '../ui'
import { MeasureValue } from './MeasureValue'
import { formatCents, formatCount, formatRatio, formatShare, formatStamp } from '../../lib/benchFormat'
import type { BenchCriteria, BenchSpecStatus, BenchValidCard } from '../../services/api'
import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

const SPEC_STATUS: Record<BenchSpecStatus, { icon: LucideIcon; tone: StatusTone }> = {
  completed: { icon: CheckCircle2, tone: 'success' },
  timeout: { icon: Clock, tone: 'warning' },
  error: { icon: XCircle, tone: 'danger' },
  budget_exceeded: { icon: Scissors, tone: 'warning' },
}

export function SpecStatusBadge({ status }: { status: BenchSpecStatus }) {
  const { t } = useTranslation()
  const { icon, tone } = SPEC_STATUS[status] ?? { icon: AlertTriangle, tone: 'neutral' as StatusTone }
  return <StatusBadge icon={icon} tone={tone} label={t(`bench.specStatus.${status}`, { defaultValue: status })} />
}

/** `passed/total (rate)` for a measured grade. */
export function useCriteriaFormat() {
  const { t, i18n } = useTranslation()
  return (c: BenchCriteria) =>
    t('bench.criteriaValue', { passed: c.passed, total: c.total, rate: formatShare(c.rate, i18n.language) })
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-meta">{label}</dt>
      <dd className="mt-0.5 text-[13px] text-gray-100">{children}</dd>
    </div>
  )
}

/**
 * The newest valid scorecard: who ran it and against what, its totals and
 * each specification's numbers, as the scorer observed them.
 */
export function BenchLatestCard({ card }: { card: BenchValidCard }) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language
  const criteria = useCriteriaFormat()
  const count = (v: number) => formatCount(v, locale)
  const share = (v: number) => formatShare(v, locale)
  const { totals } = card
  return (
    <Panel title={t('bench.latest.title', { stamp: formatStamp(card.stamp, locale) })}>
      <div className="space-y-4 p-3">
        <dl className="grid gap-3 text-xs sm:grid-cols-3 lg:grid-cols-6">
          <Fact label={t('bench.latest.llm')}><span className="chip">{card.llm}</span></Fact>
          <Fact label={t('bench.latest.policy')}>{card.policy ?? t('bench.latest.noPolicy')}</Fact>
          <Fact label={t('bench.latest.commit')}><code title={card.commit}>{card.commit.slice(0, 12)}</code></Fact>
          <Fact label={t('bench.latest.schema')}><span className="tabular-nums">{card.schema_version}</span></Fact>
          <Fact label={t('bench.latest.graders')}>
            {card.graders_sha ? <code title={card.graders_sha}>{card.graders_sha.slice(0, 12)}</code> : t('bench.latest.buildGraders')}
          </Fact>
          <Fact label={t('bench.latest.run')}>
            {card.run?.status === 'budget_exceeded'
              ? <StatusBadge icon={Scissors} tone="warning" label={t('bench.latest.budgetCut', { skipped: card.run.skipped.length })} />
              : t('bench.latest.runComplete')}
          </Fact>
        </dl>
        {card.sections_off.length > 0 && (
          <p className="text-xs text-gray-300">{t('bench.latest.sectionsOff', { sections: card.sections_off.join(', ') })}</p>
        )}
        <dl role="group" className="grid gap-3 border-t border-surface-700 pt-3 text-xs sm:grid-cols-3 lg:grid-cols-6" aria-label={t('bench.latest.totals')}>
          <Fact label={t('bench.cols.criteria')}><MeasureValue measure={totals.criteria} format={criteria} /></Fact>
          <Fact label={t('bench.cols.firstAttempt')}><span className="tabular-nums">{share(totals.first_attempt_rate)}</span></Fact>
          <Fact label={t('bench.cols.reworkPerTask')}><span className="tabular-nums">{formatRatio(totals.rework_per_task, locale)}</span></Fact>
          <Fact label={t('bench.cols.blocked')}><span className="tabular-nums">{share(totals.blocked_share)}</span></Fact>
          <Fact label={t('bench.cols.tokens')}>
            <MeasureValue measure={totals.tokens} format={count} />
            {totals.tokens.turns_unreported > 0 && (
              <span className="ml-1 text-meta">{t('bench.unreportedTurns', { count: totals.tokens.turns_unreported })}</span>
            )}
          </Fact>
          <Fact label={t('bench.cols.promptPerTask')}>
            <MeasureValue measure={totals.prompt} format={p => count(p.tokens_mean)} />
          </Fact>
        </dl>
      </div>
      <div className="overflow-x-auto">
        <table className={TABLE}>
          <caption className="sr-only">{t('bench.latest.specsCaption')}</caption>
          <thead>
            <tr>
              <th scope="col" className={TH}>{t('bench.cols.spec')}</th>
              <th scope="col" className={TH}>{t('bench.cols.status')}</th>
              <th scope="col" className={TH}>{t('bench.cols.criteria')}</th>
              <th scope="col" className={TH}>{t('bench.cols.firstAttempt')}</th>
              <th scope="col" className={TH}>{t('bench.cols.reworkPerTask')}</th>
              <th scope="col" className={TH}>{t('bench.cols.wallClock')}</th>
              <th scope="col" className={TH}>{t('bench.cols.tokens')}</th>
              <th scope="col" className={TH}>{t('bench.cols.cost')}</th>
              <th scope="col" className={TH}>{t('bench.cols.promptPerTask')}</th>
            </tr>
          </thead>
          <tbody>
            {card.specs.map(spec => (
              <tr key={spec.name} className={TR}>
                <th scope="row" className={`${TD} font-medium`}>{spec.name}</th>
                <td className={TD}><SpecStatusBadge status={spec.status} /></td>
                <td className={TD}><MeasureValue measure={spec.criteria} format={criteria} /></td>
                <td className={`${TD} tabular-nums`}>{share(spec.first_attempt_rate)}</td>
                <td className={`${TD} tabular-nums`}>{formatRatio(spec.rework_per_task, locale)}</td>
                <td className={`${TD} tabular-nums`}>{t('bench.seconds', { value: count(spec.wall_clock_seconds) })}</td>
                <td className={TD}><MeasureValue measure={spec.tokens} format={count} /></td>
                <td className={TD}><MeasureValue measure={spec.cost_cents} format={c => formatCents(c, locale)} /></td>
                <td className={TD}><MeasureValue measure={spec.prompt} format={p => count(p.tokens_mean)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
