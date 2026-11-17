import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Gauge, Lock, MessageSquareQuote, ShieldCheck, UserRoundCheck } from 'lucide-react'
import type { AgentRunListItem, BacklogItem } from '../../services/api'
import { QA_FAILURE_LIMIT, QA_REWORK_LIMIT, getQaMetrics } from '../../lib/kanban'
import { Verdict } from '../Verdict'

/*
 * A card's signals, in two groups that never mix (docs/POSITIONING.md):
 * - Observed: the Verifier's verdict and risk tier, the protected paths it
 *   matched, and whether the task's record says it waits for a person. Solid
 *   chips under a shield.
 * - Reported: what the task's messages say (QA's rework requests and failed
 *   reviews). Dashed chips under a speech mark: an agent's word, not a fact.
 */

const OBSERVED_CHIP = 'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset'
const REPORTED_CHIP = 'inline-flex items-center gap-1 rounded-md border border-dashed px-1.5 py-0.5 text-[11px] font-medium'

const RISK_TONE: Record<string, string> = {
  high: 'bg-red-950/70 text-red-200 ring-red-700/60',
  medium: 'bg-orange-950/70 text-orange-200 ring-orange-700/60',
  low: 'bg-emerald-950/60 text-emerald-200 ring-emerald-800/60',
}

function reportedTone(value: number, limit: number): string {
  if (value >= limit) return 'border-red-600/70 text-red-300'
  if (value >= Math.max(1, limit - 1)) return 'border-amber-600/70 text-amber-300'
  return 'border-surface-600 text-gray-400'
}

function GroupLabel({ icon: Icon, text, hint, compact }: {
  icon: typeof ShieldCheck; text: string; hint: string; compact: boolean
}) {
  return (
    <span title={hint}
      className={compact ? 'sr-only' : 'mr-0.5 inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-gray-500'}>
      <Icon size={11} aria-hidden="true" />{text}
    </span>
  )
}

/** A verdict on the card once the task's latest delivery has been checked. */
function DeliveryBadge({ run }: { run: AgentRunListItem }) {
  const { t } = useTranslation()
  // The accessible name contains the visible verdict word ("Delivery: Verified").
  const label = t('kanban.deliveryVerdict', {
    verdict: t(`evidence.verdict.${run.status}`),
    defaultValue: 'Delivery: {{verdict}}',
  })
  return (
    <Link to={`/evidence/${run.id}`} aria-label={label} title={label}
      className="inline-flex rounded-full hover:brightness-125">
      <Verdict status={run.status} compact />
    </Link>
  )
}

export function ObservedSignals({ item, evidenceRun, compact }: {
  item: BacklogItem; evidenceRun?: AgentRunListItem; compact: boolean
}) {
  const { t } = useTranslation()
  const observed = item.observed
  const run = evidenceRun && evidenceRun.status !== 'ingested' ? evidenceRun : undefined
  const verdict = run ? null : observed?.verdict ?? null
  const tier = observed?.risk_tier ?? null
  const areas = observed?.risk_areas ?? []
  const held = observed?.protected_paths ?? []
  const grader = areas.includes('grader')
  const waits = observed?.waits_for_person ?? false
  if (!run && !verdict && !tier && held.length === 0 && !grader && !waits) return null

  const areaNames = areas.map(area => t(`kanbanBoard.area.${area.replace(/\s+/g, '_')}`, { defaultValue: area }))
  const riskLabel = t('kanbanBoard.card.risk', { tier: t(`kanbanBoard.riskTier.${tier}`, { defaultValue: tier ?? '' }) })
  const riskTitle = areaNames.length ? `${riskLabel} — ${areaNames.join(', ')}` : riskLabel
  const heldTitle = t('kanbanBoard.card.heldTitle', { paths: held.join(', ') })

  return (
    <div role="group" aria-label={t('kanbanBoard.card.observed')} className="flex flex-wrap items-center gap-1">
      <GroupLabel icon={ShieldCheck} text={t('kanbanBoard.card.observed')} hint={t('kanbanBoard.card.observedHint')} compact={compact} />
      {run && <DeliveryBadge run={run} />}
      {verdict && (
        <span title={t('kanban.deliveryVerdict', { verdict: t(`evidence.verdict.${verdict}`) })} className="inline-flex">
          <Verdict status={verdict} compact />
        </span>
      )}
      {tier && (
        <span title={riskTitle} className={`${OBSERVED_CHIP} ${RISK_TONE[tier] ?? RISK_TONE.low}`}>
          <Gauge size={11} aria-hidden="true" />{riskLabel}
          {areaNames.length > 0 && <span className="sr-only">{` — ${areaNames.join(', ')}`}</span>}
        </span>
      )}
      {held.length > 0 && (
        <span title={heldTitle} className={`${OBSERVED_CHIP} bg-amber-950/70 text-amber-200 ring-amber-600/70`}>
          <Lock size={11} aria-hidden="true" />{t('kanbanBoard.card.held')}
          {held.length > 1 && <span aria-hidden="true" className="tabular-nums">×{held.length}</span>}
          <span className="sr-only">{`: ${held.join(', ')}`}</span>
        </span>
      )}
      {grader && (
        <span title={t('kanbanBoard.card.graderTitle')} className={`${OBSERVED_CHIP} bg-fuchsia-950/70 text-fuchsia-200 ring-fuchsia-700/60`}>
          {t('kanbanBoard.area.grader')}
        </span>
      )}
      {waits && (
        <span title={t('kanbanBoard.card.needsPersonTitle')} className={`${OBSERVED_CHIP} bg-sky-950/70 text-sky-200 ring-sky-700/60`}>
          <UserRoundCheck size={11} aria-hidden="true" />{t('kanbanBoard.card.needsPerson')}
        </span>
      )}
    </div>
  )
}

export function ReportedSignals({ item, compact }: { item: BacklogItem; compact: boolean }) {
  const { t } = useTranslation()
  const { reworks, failures } = getQaMetrics(item)
  if (reworks === 0 && failures === 0) return null
  return (
    <div role="group" aria-label={t('kanbanBoard.card.reported')} className="flex flex-wrap items-center gap-1">
      <GroupLabel icon={MessageSquareQuote} text={t('kanbanBoard.card.reported')} hint={t('kanbanBoard.card.reportedHint')} compact={compact} />
      {reworks > 0 && (
        <span className={`${REPORTED_CHIP} ${reportedTone(reworks, QA_REWORK_LIMIT)}`}
          title={t('kanban.qaReworksTitle', { count: reworks, limit: QA_REWORK_LIMIT, defaultValue: 'QA reworks: {{count}}/{{limit}}' })}>
          {t('kanban.qaReworks', { count: reworks, limit: QA_REWORK_LIMIT, defaultValue: 'QA rework {{count}}/{{limit}}' })}
        </span>
      )}
      {failures > 0 && (
        <span className={`${REPORTED_CHIP} ${reportedTone(failures, QA_FAILURE_LIMIT)}`}
          title={t('kanban.qaFailuresTitle', { count: failures, limit: QA_FAILURE_LIMIT, defaultValue: 'QA failures: {{count}}/{{limit}}' })}>
          {t('kanban.qaFailures', { count: failures, limit: QA_FAILURE_LIMIT, defaultValue: 'QA fail {{count}}/{{limit}}' })}
        </span>
      )}
    </div>
  )
}
